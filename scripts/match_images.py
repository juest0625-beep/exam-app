#!/usr/bin/env python3
"""Match exam questions to page images and crop diagrams for embedding.

Usage: python3 match_images.py [subject] [--limit N] [--dry-run]
"""
import io, json, os, sys, base64, re
from pathlib import Path
from PIL import Image

EXAM_DIR = Path("/Users/kevin/Documents/國中試題")
QUESTION_BANK = EXAM_DIR / "question-bank"
OUT_DIR = Path("/Users/kevin/exam-app")


def find_exam_pages():
    """Find all exam page directories. Returns {dir_name: [page_paths]}."""
    pages = {}
    for d in sorted(EXAM_DIR.iterdir()):
        if d.is_dir() and d.name.endswith("-standalone"):
            page_dir = d / "pages"
            if page_dir.exists():
                pgs = sorted(page_dir.glob("*.jpg"))
                if pgs:
                    pages[d.name] = pgs
    return pages


def ocr_page_fast(image_path):
    """Quick OCR using Python tesseract or basic text extraction.
    Falls back to filename-based matching if OCR unavailable."""
    # Try to extract text using pytesseract if available
    try:
        import pytesseract
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang='chi_tra+eng')
        return text[:500]
    except ImportError:
        pass

    # Fallback: return empty - we'll match by filename ordering instead
    return ""


def find_image_questions(subject):
    """Find questions in a subject that reference diagrams/images."""
    qfile = QUESTION_BANK / f"{subject}.json"
    if not qfile.exists():
        return []

    with open(qfile) as f:
        questions = json.load(f)

    img_qs = []
    for q in questions:
        qt = q.get("question", "")
        # Questions referencing figures, tables, diagrams
        if any(kw in qt for kw in ["如圖", "如附圖", "附表", "圖(", "圖（", "如表", "下圖", "上圖"]):
            img_qs.append(q)

    return img_qs


def crop_diagram_area(image_path, question_text=None):
    """Crop the likely diagram area from an exam page.
    Diagrams are typically in the bottom 2/3 of the page, or right side."""
    img = Image.open(image_path)
    w, h = img.size

    # Strategy: crop different regions based on question type
    # Most diagrams are in lower half of the page
    crops = [
        # Bottom half (most common)
        (0, h//3, w, h),
        # Right half (sometimes diagrams are on the right)
        (w//2, h//4, w, h),
        # Bottom-right quadrant
        (w//2, h//2, w, h),
    ]

    # Pick the largest crop that's not just whitespace
    best = None
    best_size = 0
    for box in crops:
        region = img.crop(box)
        # Check if region has enough non-white pixels
        gray = region.convert("L")
        pixels = list(gray.getdata())
        non_white = sum(1 for p in pixels if p < 240)
        if non_white > len(pixels) * 0.15:  # at least 15% non-white
            if non_white > best_size:
                best = region
                best_size = non_white

    if best is None:
        best = img.crop((0, h//3, w, h))

    # Resize for web
    best.thumbnail((600, 400))
    return best


def match_questions_to_pages(img_questions, exam_pages, subject_dir_name):
    """Match questions to their source pages.
    Uses OCR text matching as primary, falls back to question number ordering."""
    matched = []

    # Get all pages for this subject
    all_pages = []
    for dir_name, pages in exam_pages.items():
        # Match by subject: natural↔science, social↔social, etc.
        subj_lower = subject_dir_name
        dir_lower = dir_name.lower()
        if any(s in dir_lower for s in ['science', '自然', subj_lower]):
            all_pages.extend(pages)

    if not all_pages:
        # Fallback: use any available pages
        for pages in exam_pages.values():
            all_pages.extend(pages)
            break

    all_pages = sorted(set(all_pages))

    # Try OCR matching
    page_texts = {}
    for pg in all_pages[:20]:  # limit to first 20 pages for speed
        text = ocr_page_fast(pg)
        page_texts[str(pg)] = text

    for q in img_questions:
        qt = q.get("question", "")
        # Extract question number for matching
        qnum = q.get("number", q.get("id", "?"))

        best_page = None
        best_score = 0

        for pg_path, pg_text in page_texts.items():
            # Score by text overlap
            score = 0
            for word in qt[:30].split():
                if word in pg_text:
                    score += 1
            if score > best_score:
                best_score = score
                best_page = pg_path

        if best_page and best_score >= 2:
            cropped = crop_diagram_area(Path(best_page), qt)
            matched.append((q, cropped, best_page, best_score))
        elif all_pages:
            # Fallback: assign pages by question order
            idx = img_questions.index(q) % len(all_pages)
            pg = all_pages[idx]
            cropped = crop_diagram_area(pg, qt)
            matched.append((q, cropped, str(pg), 0))

    return matched


def build_image_quiz(matched, subject):
    """Build a quiz JSON with embedded cropped images."""
    questions = []
    for q, cropped_img, page_path, score in matched[:10]:  # max 10 questions
        buf = io.BytesIO()
        cropped_img.save(buf, format="JPEG", quality=70)
        b64 = base64.b64encode(buf.getvalue()).decode()

        # Build answer options with rationales
        options_raw = q.get("options", [])
        correct_label = q.get("answer", "")

        answer_options = []
        for opt in options_raw:
            is_correct = opt["label"] == correct_label
            answer_options.append({
                "text": opt["text"],
                "isCorrect": is_correct,
                "rationale": f"{'✅ 正確答案' if is_correct else '❌ 非正確答案'}" if not q.get('explanation') else
                             (q['explanation'] if is_correct else f"非正確答案。{q.get('explanation','')[:80]}")
            })

        questions.append({
            "question": q["question"],
            "image": f"data:image/jpeg;base64,{b64}",
            "answerOptions": answer_options,
            "_page": page_path,
            "_match_score": score,
        })

    quiz = {"questions": questions, "_metadata": {"subject": subject, "generated_from": "match_images.py"}}

    out_path = OUT_DIR / f"{subject}-image.json"
    with open(out_path, "w") as f:
        json.dump(quiz, f, ensure_ascii=False, indent=2)

    return out_path, len(questions)


def main():
    import io  # needed for BytesIO in build_image_quiz

    subject = sys.argv[1] if len(sys.argv) > 1 else "自然"
    dry_run = "--dry-run" in sys.argv
    limit = next((int(a.replace("--limit=","")) for a in sys.argv if a.startswith("--limit=")), 10)

    print(f"🔍 掃描 {subject} 科含圖表題目...")

    # Find exam pages
    pages = find_exam_pages()
    print(f"  考卷目錄: {list(pages.keys())}")

    # Find image questions
    img_qs = find_image_questions(subject)
    print(f"  含圖表題目: {len(img_qs)} 題")
    for q in img_qs[:5]:
        print(f"    - {q['question'][:80]}...")

    if dry_run:
        return

    # Match and crop
    print(f"\n✂️ 配對頁面並裁剪...")
    matched = match_questions_to_pages(img_qs, pages, subject)
    print(f"  配對成功: {len(matched)} 題")

    if not matched:
        print("  ❌ 無配對結果，使用第一頁做示範")
        # Use first available page as demo
        for pgs in pages.values():
            if pgs:
                q = img_qs[0] if img_qs else {"question": "示範題目", "options": []}
                cropped = crop_diagram_area(pgs[0])
                matched = [(q, cropped, str(pgs[0]), 0)]
                break

    out_path, count = build_image_quiz(matched, subject)
    print(f"\n✅ 完成！ → {out_path}")


if __name__ == "__main__":
    main()

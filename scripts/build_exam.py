#!/usr/bin/env python3
"""
考題數位化 Skill — 從考卷圖片到結構化題庫

完整流程：
  1. 考卷 JPG → PDF
  2. PDF → NotebookLM 擷取題目文字（run_notebooklm_extract）
  3. bounding box OCR → 找到每題在頁面上的位置
  4. 裁切每題圖片（Qn 題號 → Qn+1 題號）
  5. 組合：精準文字 + 裁切圖片 → quiz JSON
  6. QA: PDF → NotebookLM 檢查 → 修正 → 完成

Usage:
  # 完整流程（需要 NotebookLM）
  python3 build_exam.py 105science-standalone 自然

  # 只做裁切（已有題目文字時）
  python3 build_exam.py 105science-standalone 自然 --crop-only

  # 只產生 QA PDF 給 NotebookLM 檢查
  python3 build_exam.py 105science-standalone 自然 --qa-only

Output: ~/exam-app/{subject}-image.json
"""

import subprocess, json, sys, os, io, base64, re
from pathlib import Path
from PIL import Image

EXAM_DIR = Path("/Users/kevin/Documents/國中試題")
OUT_DIR = Path("/Users/kevin/exam-app")
OCR_TOOL = "/tmp/ocr_with_pos"
NOTEBOOKLM = os.path.expanduser("~/bin/notebooklm")

# ── Page structure (from NotebookLM extraction) ──
# Format: {page_filename: [question_numbers]}
# Fill this in after running NotebookLM extraction
PAGE_STRUCTURE = {}  # Will be populated per subject


def ocr_page_positions(image_path):
    """Get pixel positions of all text on a page using Vision bounding boxes."""
    result = subprocess.run(
        [OCR_TOOL, str(image_path)],
        capture_output=True, text=True, timeout=30
    )
    items = []
    for line in result.stdout.strip().split('\n'):
        m = re.match(r'Y=(\d+)\s+(.*)', line)
        if m:
            items.append((int(m.group(1)), m.group(2)))
    return items


def find_question_positions(image_path, expected_questions, text_hints=None):
    """Find Y position for each question on a page.

    Uses: 1) Question number detection (e.g., "6.")
          2) Text content matching (fallback for undetected numbers)
          3) Interpolation (last resort)

    Args:
        image_path: Path to exam page JPG
        expected_questions: List of question numbers on this page
        text_hints: Dict of {qnum: "unique text snippet from question"}

    Returns: Dict of {qnum: y_pixel}
    """
    img = Image.open(image_path)
    h = img.size[1]
    items = ocr_page_positions(image_path)

    # Method 1: Find "N." patterns
    found = {}
    for y, text in items:
        m = re.match(r'^(\d{1,2})\.', text.strip())
        if m:
            num = int(m.group(1))
            if num in expected_questions and num not in found:
                found[num] = y

    # Method 2: Find by text content for missed questions
    if text_hints:
        for qnum in expected_questions:
            if qnum not in found and qnum in text_hints:
                hint = text_hints[qnum]
                for y, text in items:
                    if hint in text:
                        found[qnum] = y
                        break

    # Sanity check: are positions properly spread?
    if len(found) >= 2:
        ys = sorted(found.values())
        if min(ys[i+1] - ys[i] for i in range(len(ys)-1)) < 30:
            # OCR positions too close — unreliable, fall back
            n = len(expected_questions)
            return {q: int(i * h / n) for i, q in enumerate(expected_questions)}

    # Method 3: Interpolate remaining
    result = {}
    for qnum in expected_questions:
        if qnum in found:
            result[qnum] = found[qnum]
        else:
            before = [(n, found[n]) for n in sorted(found) if n < qnum]
            after = [(n, found[n]) for n in sorted(found) if n > qnum]
            if before and after:
                pn, py = before[-1]; an, ay = after[0]
                result[qnum] = int(py + (qnum-pn)/(an-pn) * (ay-py))
            elif before:
                result[qnum] = before[-1][1] + int(h / len(expected_questions))
            elif after:
                result[qnum] = max(0, after[0][1] - int(h / len(expected_questions)))
            else:
                result[qnum] = int(expected_questions.index(qnum) * h / len(expected_questions))

    return result


def crop_question(image_path, y_start, y_end, top_margin=20):
    """Crop a question from a page image."""
    img = Image.open(image_path)
    w, h = img.size
    y1 = max(0, y_start - top_margin)
    y2 = min(h, y_end - 5)
    cropped = img.crop((0, y1, w, y2))
    cropped.thumbnail((750, 420))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=82)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"


def build_quiz(questions_data, page_structure, exam_dir_name, top_margin=20):
    """Main pipeline: position → crop → build quiz JSON.

    Args:
        questions_data: Dict of {qnum: {page, q, opts, text_hint}}
        page_structure: Dict of {page_filename: [qnums]}
        exam_dir_name: e.g., "105science-standalone"
        top_margin: px above question number to start crop
    """
    pages_dir = EXAM_DIR / exam_dir_name / "pages"

    # Get positions for all pages
    all_positions = {}
    for pg_name, expected in page_structure.items():
        pg_path = pages_dir / pg_name
        hints = {qnum: questions_data[qnum].get("text_hint", "")
                 for qnum in expected if qnum in questions_data}
        positions = find_question_positions(pg_path, expected, hints)
        all_positions[pg_name] = positions

        print(f"\n{pg_name}:")
        for qnum in expected:
            marker = "📍" if any(re.match(rf'^{qnum}\.', t)
                          for _, t in ocr_page_positions(pg_path)) else "📝"
            print(f"  Q{qnum:2d} @ Y={positions[qnum]:4d}px {marker}")

    # Crop each question
    quiz_questions = []
    for qnum in sorted(questions_data.keys()):
        d = questions_data[qnum]
        pg_name = d["page"]
        expected = page_structure[pg_name]
        positions = all_positions[pg_name]

        this_y = positions[qnum]
        idx = expected.index(qnum)
        next_y = positions[expected[idx + 1]] if idx + 1 < len(expected) else \
                  Image.open(pages_dir / pg_name).size[1]

        b64 = crop_question(pages_dir / pg_name, this_y, next_y, top_margin)

        # ── Quality check: does this question have ABCD options? ──
        region_text = ' '.join(t for y, t in ocr_page_positions(pages_dir / pg_name)
                              if max(0,this_y-top_margin) <= y <= min(Image.open(pages_dir/pg_name).size[1], next_y-5))
        has_options = bool(re.search(r'[（(][A-D][）)]', region_text))
        quality_flag = "" if has_options else " ⚠️ 缺選項"

        quiz_questions.append({
            "question": f"{qnum}. {d['q']}" if qnum in questions_data else f"{qnum}. {region_text[:200]}",
            "image": b64,
            "answerOptions": [{"text": t, "isCorrect": c, "rationale": r}
                            for t, c, r in d["opts"]] if qnum in questions_data else
                            [{"text": f"⚠️ 需校正", "isCorrect": False, "rationale": ""} for _ in range(4)],
            "_quality_ok": has_options,
        })
        print(f"Q{qnum}: Y={max(0,this_y-top_margin)}→{min(Image.open(pages_dir/pg_name).size[1], next_y-5)}, {len(b64)//1024}KB{quality_flag}")

    return {"questions": quiz_questions}


def export_qa_pdf(quiz, output_path):
    """Export quiz questions as a PDF for NotebookLM QA."""
    images = []
    for q in quiz["questions"]:
        img_data = base64.b64decode(q["image"].split(",")[1])
        im = Image.open(io.BytesIO(img_data))
        if im.mode == 'RGBA':
            im = im.convert('RGB')
        images.append(im)
    images[0].save(output_path, save_all=True, append_images=images[1:])
    return output_path


def run_notebooklm_extract(exam_dir_name, subject_name):
    """Guide for NotebookLM extraction step.

    Manual steps:
    1. Convert exam pages to PDF: python3 build_exam.py --make-pdf {exam_dir}
    2. Upload PDF to NotebookLM
    3. Ask: "列出每一題的完整題目、四個選項、圖表描述"
    4. Copy the extracted text here
    """
    pages_dir = EXAM_DIR / exam_dir_name / "pages"
    images = [Image.open(p) for p in sorted(pages_dir.glob("*.jpg"))[1:]]
    if not images:
        print("No pages found")
        return

    pdf_path = OUT_DIR / f"{exam_dir_name}.pdf"
    imgs_rgb = [im.convert('RGB') if im.mode == 'RGBA' else im for im in images]
    imgs_rgb[0].save(pdf_path, save_all=True, append_images=imgs_rgb[1:])

    print(f"📄 PDF 已產生: {pdf_path} ({len(imgs_rgb)} 頁)")
    print(f"")
    print(f"下一步：")
    print(f"  1. 上傳 PDF 到 NotebookLM")
    print(f"     {NOTEBOOKLM} source add {pdf_path} --type file")
    print(f"  2. 問 NotebookLM 擷取題目")
    print(f"  3. 將題目文字填入 PAGE_STRUCTURE 和 questions_data")
    print(f"  4. 重新執行: python3 build_exam.py {exam_dir_name} {subject_name}")


# ── Demo: 105自然科 ──
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n範例: python3 build_exam.py 105science-standalone 自然")
        sys.exit(0)

    exam_dir = sys.argv[1]
    subject = sys.argv[2] if len(sys.argv) > 2 else "自然"

    if "--make-pdf" in sys.argv:
        run_notebooklm_extract(exam_dir, subject)
        sys.exit(0)

    # This is where you'd load questions_data from a JSON file
    # For now, it demonstrates the pipeline with the 105自然 data
    print(f"🔧 考題數位化 Skill — {exam_dir} {subject}科")
    print(f"   請先在程式碼中填入 PAGE_STRUCTURE 和 questions_data")
    print(f"   或使用 --make-pdf 先產生 PDF 給 NotebookLM 擷取")

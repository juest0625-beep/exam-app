#!/usr/bin/env python3
"""Extract individual questions from exam pages: OCR text + cropped image for each question.

Usage: python3 extract_questions.py [subject_dir] [--limit N]
Output: exam-app/questions/{subject}/{question_id}.json + .jpg
"""
import subprocess, json, os, sys, re, io, base64
from pathlib import Path
from PIL import Image

EXAM_DIR = Path("/Users/kevin/Documents/國中試題")
OUT_DIR = Path("/Users/kevin/exam-app/questions")

def ocr_page(image_path):
    """OCR a single exam page, return text."""
    result = subprocess.run(
        ["/tmp/ocr_exam", str(image_path)],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()

def find_question_regions(page_text, image_path):
    """Parse OCR text to find approximate question boundaries on page.
    Returns list of (question_number, y_start, y_end)."""
    img = Image.open(image_path)
    h = img.size[1]

    # Find question number patterns like "1.", "12.", "1  ", "1."
    # These mark the start of a new question
    matches = list(re.finditer(r'(?:^|\n)\s*(\d{1,2})[\s\.\)]\s*', page_text, re.MULTILINE))

    if not matches:
        # Fallback: split page into equal parts
        parts = 4
        regions = []
        for i in range(parts):
            regions.append((f"Q{i+1}", int(h * i / parts), int(h * (i+1) / parts)))
        return regions

    # Convert text positions to image y-coordinates (rough estimate)
    lines = page_text.split('\n')
    total_lines = len(lines)

    regions = []
    for m in matches:
        qnum = m.group(1)
        line_pos = page_text[:m.start()].count('\n')
        y_start = int(h * line_pos / max(total_lines, 1))

        # End at next question or page bottom
        next_match = re.search(r'(?:^|\n)\s*\d{1,2}[\s\.\)]', page_text[m.end():], re.MULTILINE)
        if next_match:
            end_pos = m.end() + next_match.start()
            end_line = page_text[:end_pos].count('\n')
            y_end = int(h * end_line / max(total_lines, 1))
        else:
            y_end = h

        regions.append((qnum, y_start, y_end))

    return regions

def crop_question(image_path, y_start, y_end, padding=20):
    """Crop a question region from the page image."""
    img = Image.open(image_path)
    w = img.size[0]
    y1 = max(0, y_start - padding)
    y2 = min(img.size[1], y_end + padding)
    return img.crop((0, y1, w, y2))

def extract_subject(subject_dir, limit=None):
    """Extract all questions from a subject directory."""
    pages_dir = EXAM_DIR / subject_dir / "pages"
    if not pages_dir.exists():
        print(f"  ❌ {pages_dir} not found")
        return 0

    pages = sorted(pages_dir.glob("*.jpg"))
    print(f"  📖 {len(pages)} 頁")

    subject_name = subject_dir.replace("-standalone", "").replace("105", "").replace("106", "")
    # Map English names
    name_map = {"english": "英文", "math": "數學", "science": "自然", "social": "社會", "chinese": "國文"}
    for eng, chi in name_map.items():
        if eng in subject_dir.lower():
            subject_name = chi
            break

    out_subject = OUT_DIR / subject_name
    out_subject.mkdir(parents=True, exist_ok=True)

    question_count = 0
    qid = 1

    for page in pages:
        if limit and question_count >= limit:
            break

        print(f"    {page.name}...", end=" ")

        # OCR
        text = ocr_page(page)

        # Find question regions
        regions = find_question_regions(text, page)

        for qnum, y1, y2 in regions:
            if limit and question_count >= limit:
                break

            # Crop question
            cropped = crop_question(page, y1, y2)

            # Extract question text from the crop area
            # (simple approach: take the OCR text between markers)

            # Save image
            img_path = out_subject / f"q{qid:03d}.jpg"
            cropped.save(img_path, quality=80, optimize=True)

            # Save metadata
            meta = {
                "id": qid,
                "subject": subject_name,
                "source_page": page.name,
                "question_number": qnum,
                "year": subject_dir[:3],
            }
            with open(out_subject / f"q{qid:03d}.json", "w") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            qid += 1
            question_count += 1

        print(f"{len(regions)}題")

    return question_count

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("subject", nargs="?", help="Subject dir name (e.g. 105science-standalone)")
    p.add_argument("--limit", type=int, default=20, help="Max questions per subject")
    p.add_argument("--all", action="store_true", help="Process all subjects")
    args = p.parse_args()

    if args.all:
        subjects = [d.name for d in sorted(EXAM_DIR.iterdir()) if d.is_dir() and d.name.endswith("-standalone")]
    elif args.subject:
        subjects = [args.subject]
    else:
        subjects = ["105science-standalone"]

    total = 0
    for subj in subjects:
        print(f"\n📚 {subj}")
        n = extract_subject(subj, args.limit)
        total += n

    print(f"\n✅ 完成: {total} 題 (跨 {len(subjects)} 科目)")
    print(f"   輸出: {OUT_DIR}")

if __name__ == "__main__":
    main()

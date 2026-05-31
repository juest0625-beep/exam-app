#!/usr/bin/env python3
"""Generate subject-specific quiz questions in Traditional Chinese using DeepSeek."""
import json, os, urllib.request, sys

API_KEY = os.environ.get("OPENROUTER_API_KEY", "YOUR_KEY_HERE")
API_URL = "https://openrouter.ai/api/v1/chat/completions"

subjects = sys.argv[1:] if len(sys.argv) > 1 else ["數學", "國文", "自然", "社會"]

for subject in subjects:
    print(f"=== 生成 {subject} ===")

    # Read sample questions
    with open(f"/Users/kevin/Documents/國中試題/question-bank/{subject}.json") as f:
        all_qs = json.load(f)
    samples = all_qs[:10]
    sample_text = ""
    for q in samples:
        sample_text += f"「{q['question']}」答案: {q['answer']}\n"

    prompt = f"""你是台灣國中會考命題老師。請根據以下考古題的出題風格和難度，用繁體中文出10題國中會考{subject}科模擬試題。

參考考古題：
{sample_text}

要求：
- 所有題目、選項、詳解都必須是繁體中文
- 數學科用 ÷ × + − = 等一般符號，不要用 LaTeX（不用 \\times \\div）
- 每題4個選項，只有1個正確答案
- 每題附詳解，說明為什麼正確和其他選項為什麼錯
- 題目難度貼近真實會考

請嚴格用以下JSON格式回覆（只回JSON，不要其他文字）：
{{"questions":[{{"question":"題目","answerOptions":[{{"text":"選項A","isCorrect":true,"rationale":"詳解"}},{{"text":"選項B","isCorrect":false,"rationale":"詳解"}},{{"text":"選項C","isCorrect":false,"rationale":"詳解"}},{{"text":"選項D","isCorrect":false,"rationale":"詳解"}}]}}, ...]}}"""

    data = json.dumps({
        "model": "deepseek/deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "temperature": 0.5,
    }).encode()

    req = urllib.request.Request(API_URL, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]

        # Parse JSON from response
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        # Fix LaTeX escaping: replace \\cmd with plain text equivalents
        content = content.replace('\\\\times', '×')
        content = content.replace('\\\\div', '÷')
        content = content.replace('\\\\cdot', '·')
        content = content.replace('\\\\frac', '')
        content = content.replace('\\\\sqrt', '√')
        content = content.replace('\\\\pm', '±')
        content = content.replace('$', '')

        content = content.strip()
        # Remove trailing commas before closing brackets
        import re
        content = re.sub(r',\s*}', '}', content)
        content = re.sub(r',\s*]', ']', content)

        try:
            quiz = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract just the questions array
            start = content.find('"questions"')
            if start > 0:
                content = '{' + content[start:]
                content = re.sub(r',\s*}', '}', content)
                content = re.sub(r',\s*]', ']', content)
            try:
                quiz = json.loads(content)
            except json.JSONDecodeError as e2:
                print(f"  JSON parse error: {e2}")
                print(f"  Raw content preview: {content[:200]}")
                raise

        # Save
        out_path = f"/Users/kevin/exam-app/{subject}.json"
        with open(out_path, "w") as f:
            json.dump(quiz, f, ensure_ascii=False, indent=2)

        q0 = quiz["questions"][0]
        print(f"  ✅ {len(quiz['questions'])} 題")
        print(f"  範例: {q0['question'][:100]}")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")

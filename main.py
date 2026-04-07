from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import whisper
import sqlite3
import json
import os
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env

app = FastAPI()

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Whisper model - using "tiny" for faster CPU processing
print("Loading Whisper model (tiny)...")
model = whisper.load_model("tiny")
print("Whisper model loaded!")

# Database setup
def init_db():
    conn = sqlite3.connect('meetings.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS meetings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  filename TEXT,
                  transcript TEXT,
                  summary TEXT,
                  action_items TEXT,
                  created_at TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# ---------- Summarization with GLM-4.7-Flash (Cloud, free) ----------
async def summarize_with_llm(transcript: str) -> dict:
    client = OpenAI(
        api_key=os.getenv("ZHIPU_API_KEY"),
        base_url="https://open.bigmodel.cn/api/paas/v4/"
    )
    
    response = client.chat.completions.create(
        model="glm-4-flash",
        messages=[
            {"role": "system", "content": "Summarize the following meeting transcript and extract action items in JSON format. Return a JSON object with keys 'summary' (string) and 'action_items' (list of strings)."},
            {"role": "user", "content": transcript}
        ],
        response_format={"type": "json_object"}
    )
    
    return json.loads(response.choices[0].message.content)

# ---------- Alternative: local LFM2-2.6B-Transcript (uncomment to use) ----------
# from transformers import AutoModelForCausalLM, AutoTokenizer
# import torch
# 
# async def summarize_with_llm_local(transcript: str) -> str:
#     model_name = "LiquidAI/LFM2-2.6B-Transcript"
#     tokenizer = AutoTokenizer.from_pretrained(model_name)
#     model = AutoModelForCausalLM.from_pretrained(
#         model_name,
#         torch_dtype=torch.float16,
#         device_map="auto"
#     )
#     prompt = f"Analyze this transcript and provide a summary and action items:\n\n{transcript}"
#     inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
#     with torch.no_grad():
#         outputs = model.generate(**inputs, max_new_tokens=500)
#     return tokenizer.decode(outputs[0], skip_special_tokens=True)
# 
# # Then replace the call in /upload with summarize_with_llm_local

@app.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    file_path = f"temp_{file.filename}"
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    try:
        # Step 1: Transcribe with Whisper
        result = model.transcribe(file_path, fp16=False)
        transcript = result["text"]
        
        # Step 2: Summarize (using cloud LLM)
        summary_result = await summarize_with_llm(transcript)
        
        # Step 3: Save to database
        conn = sqlite3.connect('meetings.db')
        c = conn.cursor()
        c.execute(
            "INSERT INTO meetings (filename, transcript, summary, action_items, created_at) VALUES (?, ?, ?, ?, ?)",
            (file.filename, transcript, summary_result["summary"],
             json.dumps(summary_result["action_items"]), datetime.now())
        )
        conn.commit()
        meeting_id = c.lastrowid
        conn.close()
        
        os.remove(file_path)
        return {
            "id": meeting_id,
            "transcript": transcript,
            "summary": summary_result["summary"],
            "action_items": summary_result["action_items"]
        }
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
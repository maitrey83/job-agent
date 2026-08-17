from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify
import os
from agent import process_application
from fpdf import FPDF
import pathlib
from pypdf import PdfReader
from docx import Document
import io

app = Flask(__name__)
# Output folder is less relevant for cloud, but we'll keep it for local consistency
# Ideally we switch to memory buffers for cloud
app.config['OUTPUT_FOLDER'] = 'output'
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

# Optional shared-secret for the programmatic API below. Set APPLIER_API_KEY
# in .env (locally) / Render's environment variables (deployed) to require
# callers to send it back as the X-API-Key header. If unset, the endpoint is
# open - fine for quick local testing, not recommended once this is deployed
# publicly, since every call spends your Gemini quota.
API_KEY = os.environ.get('APPLIER_API_KEY')

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 12)
        # self.cell(0, 10, 'Job Application', 0, 1, 'C')

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def sanitize_text(text):
    """Replaces common unicode characters with Latin-1 equivalents."""
    replacements = {
        '\u2018': "'",  # Left single quote
        '\u2019': "'",  # Right single quote
        '\u201c': '"',  # Left double quote
        '\u201d': '"',  # Right double quote
        '\u2013': '-',  # En dash
        '\u2014': '--', # Em dash
        '\u2026': '...', # Ellipsis
        '\u00a0': ' ',  # Non-breaking space
    }
    for search, replace in replacements.items():
        text = text.replace(search, replace)
    
    # Finally encode to latin-1, ignoring unmappable, then back to utf-8 (or just str) 
    # so FPDF doesn't crash on other chars
    return text.encode('latin-1', 'replace').decode('latin-1')

def generate_pdf(text):
    """Generates PDF and returns it as a bytes buffer."""
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    
    # Sanitize text to avoid Unicode errors with standard fonts
    text = sanitize_text(text)
    
    pdf.multi_cell(0, 8, text)
    
    # Output to memory buffer
    buffer = io.BytesIO()
    # fpdf2's output method accepts a buffer if you pass 'dest' argument is confusing
    # actually fpdf2.output() with no args returns bytes in recent versions, 
    # OR we can write to a buffer.
    # Let's use the explicit byte return
    pdf_bytes = pdf.output() 
    buffer.write(pdf_bytes)
    buffer.seek(0)
    return buffer

def extract_text_from_file(file):
    """Extracts text from uploaded PDF, DOCX, or TXT file."""
    filename = file.filename.lower()
    
    if filename.endswith('.pdf'):
        try:
            reader = PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            raise Exception(f"Error reading PDF: {e}")
            
    elif filename.endswith('.docx'):
        try:
            doc = Document(file)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        except Exception as e:
            raise Exception(f"Error reading DOCX: {e}")
            
    else:
        # Default to text/plain decoding
        try:
            return file.read().decode('utf-8')
        except Exception as e:
            raise Exception(f"Error reading text file: {e}")

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        job_desc = request.form.get('job_desc')
        resume_content = request.form.get('resume_content')
        contact_name = request.form.get('contact_name')
        contact_role = request.form.get('contact_role')
        personal_note = request.form.get('personal_note')
        fit_note = request.form.get('fit_note')
        message_type = request.form.get('message_type', 'role_specific')
        specific_hook = request.form.get('specific_hook')
        raw_questions = request.form.get('application_questions', '')
        
        # Split questions by newline and filter out empty lines
        questions = [q.strip() for q in raw_questions.split('\n') if q.strip()]
        
        # If file provided, read it? For now, let's assume text paste for resume to be simple
        # Or if "resume_file" in request.files...
        if 'resume_file' in request.files and request.files['resume_file'].filename:
            file = request.files['resume_file']
            resume_content = extract_text_from_file(file)
        
        # Run agent
        try:
            results = process_application(
                job_desc=job_desc,
                resume=resume_content,
                contact_name=contact_name,
                contact_role=contact_role,
                personal_note=personal_note,
                fit_note=fit_note,
                message_type=message_type,
                specific_hook=specific_hook,
                questions=questions,
                output_dir=app.config['OUTPUT_FOLDER']
            )
            
            # Generate PDF for cover letter (InMemory)
            company_name = results.get('company_name', 'Company')
            # Sanitize for PDF filename just in case
            safe_name = "".join([c for c in company_name if c.isalnum() or c in ('_', '-')]).strip()
            safe_name = safe_name[:50] # Truncate for safety
            if not safe_name: safe_name = "Company"
            
            pdf_filename = f'cover_letter_{safe_name}.pdf'
            
            import tempfile
            tmp_dir = tempfile.gettempdir()
            pdf_path = os.path.join(tmp_dir, pdf_filename)
            
            # Generate and save to tmp
            pdf_buffer = generate_pdf(results['cover_letter_text'])
            with open(pdf_path, 'wb') as f:
                f.write(pdf_buffer.getbuffer())
            
            return render_template('results.html', 
                                   cover_letter=results['cover_letter_text'],
                                   email=results['outreach_email_text'],
                                   question_answers=results['question_answers'],
                                   pdf_link=f'/download_tmp/{pdf_filename}')
                                   
        except Exception as e:
            return f"Error: {e}", 500

    return render_template('index.html')

@app.route('/download_tmp/<filename>')
def download_tmp(filename):
    import tempfile
    tmp_dir = tempfile.gettempdir()
    return send_file(os.path.join(tmp_dir, filename), as_attachment=True)


@app.route('/api/generate-packet', methods=['POST'])
def api_generate_packet():
    """
    JSON API used by Applier-Engine (src/ai/jobAgentClient.ts) to get a
    tailored cover letter, a JD-vs-resume fit score, and answers to any
    screening questions the form-filler couldn't resolve on its own.

    Request body:
      {
        "jobDescription": "...",       # required - raw JD text (Applier-Engine scrapes this from the page)
        "resumeText": "...",           # required - plain-text resume
        "unmappedQuestions": ["..."]   # optional - question labels Applier-Engine's form-filler couldn't map
      }

    Response body:
      {
        "companyName": "...",
        "coverLetter": "...",
        "fitScore": 0.0-1.0,
        "questionAnswers": [{"question": "...", "answer": "...", "confidence": 0.0-1.0}, ...]
      }
    """
    if API_KEY:
        provided_key = request.headers.get('X-API-Key')
        if provided_key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    job_description = (payload.get('jobDescription') or '').strip()
    resume_text = (payload.get('resumeText') or '').strip()
    unmapped_questions = payload.get('unmappedQuestions') or []

    if not job_description or not resume_text:
        return jsonify({"error": "jobDescription and resumeText are both required"}), 400

    try:
        results = process_application(
            job_desc=job_description,
            resume=resume_text,
            contact_name=None,
            contact_role=None,
            questions=unmapped_questions,
            output_dir=app.config['OUTPUT_FOLDER'],
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "companyName": results.get('company_name', 'Company'),
        "coverLetter": results.get('cover_letter_text', ''),
        "fitScore": results.get('fit_score', 0.5),
        "questionAnswers": results.get('question_answers_detailed', []),
    })


if __name__ == '__main__':
    app.run(debug=True, port=8000)

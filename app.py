from flask import Flask, render_template, request, send_file, redirect, url_for
import os
from agent import process_application
from fpdf import FPDF
import pathlib
from pypdf import PdfReader
from docx import Document

app = Flask(__name__)
# Ensure static folder and output folder setup
app.config['OUTPUT_FOLDER'] = 'output'
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

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

def generate_pdf(text, filename):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    
    # Sanitize text to avoid Unicode errors with standard fonts
    text = sanitize_text(text)
    
    pdf.multi_cell(0, 8, text)
    
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    pdf.output(output_path)
    pdf.output(output_path)
    return output_path

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
                output_dir=app.config['OUTPUT_FOLDER']
            )
            
            # Generate PDF for cover letter
            company_name = results.get('company_name', 'Company')
            # Sanitize for PDF filename just in case
            safe_name = "".join([c for c in company_name if c.isalnum() or c in ('_', '-')]).strip()
            safe_name = safe_name[:50] # Truncate for safety
            if not safe_name: safe_name = "Company"
            
            pdf_filename = f'cover_letter_{safe_name}.pdf'
            pdf_path = generate_pdf(results['cover_letter_text'], pdf_filename)
            
            return render_template('results.html', 
                                   cover_letter=results['cover_letter_text'],
                                   email=results['outreach_email_text'],
                                   pdf_link=f'/download/{pdf_filename}')
                                   
        except Exception as e:
            return f"Error: {e}", 500

    return render_template('index.html')

@app.route('/download/<filename>')
def download(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=8000)

import click
import os
import pathlib
import dotenv
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

# Load environment variables and configure API
dotenv.load_dotenv()

try:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    # NOTE: User found 'gemini-1.5-flash' works for them.
    # Using that model name as it's confirmed to work in their environment.
    model = genai.GenerativeModel('gemini-2.5-flash')
except Exception as e:
    print(f"Error configuring AI model: {e}")
    model = None

# --- Helper function to call the Gemini API ---
def generate_content(prompt):
    """Sends a prompt to the Gemini API and returns the response."""
    if not model:
        return "AI model is not configured. Please check your API key."
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred while generating content: {e}"

# --- Helper function to fetch job description ---
def fetch_job_description(input_source):
    """Fetches job description from a URL or a file path."""
    if input_source.startswith("http://") or input_source.startswith("https://"):
        try:
            response = requests.get(input_source)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            # Extract text and clean up
            text = soup.get_text(separator='\n')
            # Basic cleanup: remove excessive whitespace
            clean_text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])
            return clean_text
        except Exception as e:
            raise click.ClickException(f"Error fetching URL: {e}")
    else:
        # Assume it's a file path
        try:
            return pathlib.Path(input_source).read_text()
        except Exception as e:
             raise click.ClickException(f"Error reading file {input_source}: {e}")

# --- New: Helper functions to create specific prompts ---
def create_cover_letter_prompt(jd_content, resume_content):
    """Creates a detailed prompt for generating a cover letter."""
    return f"""
    Act as an expert career coach and professional writer. Based on the provided resume and job description,
    write a professional, concise, and compelling cover letter. The cover letter should be three paragraphs long.
    - Paragraph 1: State the position being applied for and express enthusiasm for the company and role.
    - Paragraph 2: Highlight 2-3 key experiences from the resume that directly align with the most important
      responsibilities in the job description. Connect the candidate's skills to the company's needs.
    - Paragraph 3: Reiterate interest and include a clear call to action (e.g., "I am eager to discuss...").
    
    IMPORTANT: if details (like company address, hiring manager name, or specific numbers) are not present in the
    job description, do NOT make them up. Omit them entirely.

    --- JOB DESCRIPTION ---
    {jd_content}

    --- RESUME ---
    {resume_content}
    """
    
def extract_company_name(jd_content):
    """Extracts the company name from the job description using AI."""
    prompt = f"""
    Extract the company name from the following job description text.
    Return ONLY the company name as a string. Do not include "Company Name:" or any other text.
    If the company name is not clearly found, return "Company".
    
    --- JOB DESCRIPTION ---
    {jd_content[:2000]} 
    """ # Truncate to save tokens, usually name is at top
    result = generate_content(prompt).strip()
    
    # Check for error messages in the result
    if "Error" in result or len(result) > 100:
        return "Company"
        
    return result.replace(" ", "_").replace("/", "-")

def create_outreach_email_prompt(jd_content, resume_content, contact_name, contact_role, personal_note=None, fit_note=None):
    """Creates a prompt for a referral request or outreach email."""
    
    personal_note_instruction = ""
    if personal_note:
        personal_note_instruction = f'''
    --- PERSONAL NOTE TO INCLUDE ---
    "{personal_note}"
    
    Use the personal note above to create a warm and genuine opening for the email.
    '''

    fit_note_instruction = """
    Then, briefly summarize the candidate's fit for the role, based on the provided resume and job description.
    """
    if fit_note:
        fit_note_instruction = f'''
    --- FIT NOTE TO INCLUDE ---
    "{fit_note}"

    Use this exact sentence above to describe why the candidate is a good fit for the role.
    '''

    return f"""
    Act as a professional networking expert. Your task is to write a short, genuine, and personal outreach email
    to a contact named {contact_name}, who is a {contact_role}. The entire message must be under 200 characters.
    Avoid generic template language and adopt a warm, respectful, and human-like tone.
    {personal_note_instruction}
    The email's goal is to express interest in a role and respectfully ask for a referral or a brief chat.
    {fit_note_instruction}
    --- JOB DESCRIPTION ---
    {jd_content}

    --- RESUME ---
    {resume_content}
    """

# --- Core Logic Function (Reusable) ---
def process_application(job_desc, resume, contact_name, contact_role, personal_note, fit_note, output_dir):
    """
    Core logic to process the application generation.
    Returns a dictionary with paths to generated files and content.
    """
    jd_content = fetch_job_description(job_desc)
    
    # Handle resume input (file path or direct content)
    # If it looks like a file path and exists, read it. Otherwise treat as content.
    resume_content = ""
    try:
        if os.path.exists(resume):
             resume_content = pathlib.Path(resume).read_text()
        else:
             resume_content = resume
    except OSError:
        # If it's too long to be a filename or invalid path, treat as content
        resume_content = resume

    print("🧠 Generating Cover Letter...")
    cover_letter_prompt = create_cover_letter_prompt(jd_content, resume_content)
    cover_letter_text = generate_content(cover_letter_prompt)
    
    # Extract company name for filename
    company_name = extract_company_name(jd_content)
    # Sanitize filename
    safe_company_name = "".join([c for c in company_name if c.isalnum() or c in ('_', '-')]).strip()
    # TRUNCATE to avoid "File name too long" errors if AI hallucinates or returns error text
    safe_company_name = safe_company_name[:50] 
    
    if not safe_company_name:
        safe_company_name = "Company"
        
    print(f"🏢 Identified Company: {safe_company_name}")

    outreach_email_text = ""
    if contact_name and contact_role:
           print(f"🧠 Generating Outreach Email for {contact_name}...")
           outreach_email_prompt = create_outreach_email_prompt(jd_content, resume_content, contact_name, contact_role, personal_note, fit_note)
           outreach_email_text = generate_content(outreach_email_prompt)
   
    print("💾 Saving generated files...")
    output_path = pathlib.Path(output_dir)
    output_path.mkdir(exist_ok=True)
   
    cover_filename = f'cover_letter_{safe_company_name}.txt'
    cover_letter_path = output_path / cover_filename
    cover_letter_path.write_text(cover_letter_text)
    print(f"   -> Saved Cover Letter to {cover_letter_path}")

    outreach_email_path = None
    if outreach_email_text:
        outreach_email_path = output_path / 'outreach_email.txt'
        outreach_email_path.write_text(outreach_email_text)
        print(f"   -> Saved Outreach Email to {outreach_email_path}")

    return {
        "cover_letter_text": cover_letter_text,
        "outreach_email_text": outreach_email_text,
        "cover_letter_path": str(cover_letter_path),
        "outreach_email_path": str(outreach_email_path) if outreach_email_path else None,
        "company_name": safe_company_name
    }

@click.group()
def cli():
    """
    A Job Application Agent to automatically create cover letters,
    tailor resumes, and generate outreach emails.
    """
    pass

@cli.command()
@click.option('--job-desc', '-j', required=False, help='Path to the job description file or a URL.')
@click.option('--resume', '-r', required=False, type=click.Path(exists=True, dir_okay=False), help='Path to your base resume text file.')
@click.option('--contact-name', '-n', help='Name of the contact for the outreach email.')
@click.option('--contact-role', '-c', help='Role/relationship of the contact (e.g., "Recruiter").')
@click.option('--personal-note', '-p', help='A personal note to include in the outreach email.')
@click.option('--fit-note', '-f', help='A specific sentence about why you are a good fit.')
@click.option('--output-dir', '-o', default='output', help='Directory to save the generated files.')
def create_packet(job_desc, resume, contact_name, contact_role, personal_note, fit_note, output_dir):
    """
    Generates a complete job application packet.
    """
    click.echo("🚀 Starting the Job Application Agent...")

    try:
        # Default Resume Logic
        if not resume:
             resume = os.getenv('RESUME_PATH')
        
        # Interactive Prompts
        if not job_desc:
            job_desc = click.prompt("Please enter the job description file path or URL")
        
        if not resume:
             resume = click.prompt("Please enter the resume file path", type=click.Path(exists=True, dir_okay=False))

        # Call the core logic
        process_application(job_desc, resume, contact_name, contact_role, personal_note, fit_note, output_dir)
        
        click.echo("✅ Successfully saved all files.")
        click.echo("\n🎉 Job application packet created successfully! 🎉")

    except Exception as e:
        click.echo(f"❌ Error: {e}")

   
   
if __name__ == '__main__':
       cli()
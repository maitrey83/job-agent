import click
import os
import pathlib
import dotenv
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import json
import re

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
        # Standard model.generate_content call
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
        # Assume it's a file path unless it looks like a long text blob
        try:
            if "\n" in input_source or len(input_source) > 255:
                # Direct text input
                return input_source
            
            path = pathlib.Path(input_source)
            if path.exists() and path.is_file():
                return path.read_text()
            
            return input_source
        except Exception:
             return input_source

# --- New: Helper functions to create specific prompts ---
def create_cover_letter_prompt(jd_content, resume_content):
    """Creates a detailed prompt for generating a cover letter."""
    return f"""
    Act as an expert career coach and professional writer. Based on the provided resume and job description,
    write a professional, concise, and compelling cover letter. The cover letter should be three paragraphs long.
    - Paragraph 1: State the position being applied for and express enthusiasm for the company and role.
    - Paragraph 2: Highlight 2-3 key experiences from the resume that directly align with the most important
      responsibilities in the job description. Connect the candidate's skills to the company's needs.
    - Paragraph 3: Reiterate interest and include a call to action (e.g., "I am eager to discuss...").
    
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

def create_combined_prompt(jd_content, resume_content, contact_name, contact_role, personal_note=None, fit_note=None, message_type='role_specific', specific_hook=None, questions=None):
    """Creates a single prompt to generate everything at once."""
    
    outreach_instructions = ""
    if contact_name and contact_role:
        
        # Build the shared context
        shared_context = ""
        if personal_note: shared_context += f'Personal Note: "{personal_note}"\n'
        if fit_note: shared_context += f'Fit Note: "{fit_note}"\n'
        if specific_hook: shared_context += f'Specific Hook/Research: "{specific_hook}"\n'

        # Select strategy instruction
        strategy_instruction = ""
        if message_type == 'exploratory':
            strategy_instruction = """
            - TYPE: Exploratory / Networking Code (NOT a job application).
            - GOAL: Ask for a 10-min virtual coffee to ask 1-2 specific questions and learn from them.
            - DO NOT ask for a referral. 
            - Use the "Specific Hook" to explain why you want to talk to THEM specifically.
            """
        else: # role_specific
            strategy_instruction = """
            - TYPE: Role-Specific Application.
            - GOAL: Express interest in the open role and ask for a brief chat to learn about their team's challenges.
            - Use the "Fit Note" to connect a key skill to their problem.
            - Use the "Specific Hook" if provided to show you did research.
            """

        outreach_instructions = f"""
        2. "outreach_email": A short connection request/email to {contact_name} ({contact_role}).
           - STRICT LIMIT: Must be under 300 characters.
           - TONE: Warm, genuine, human. NO generic fluff like "I hope this finds you well".
           - CONTEXT:
             {shared_context}
           - STRATEGY:
             {strategy_instruction}
        """
    else:
        outreach_instructions = '2. "outreach_email": null (Return null as no contact info provided)'

    questions_instruction = ""
    if questions:
        formatted_questions = "\n".join([f"- {q}" for q in questions])
        questions_instruction = f"""
    5. "question_answers": A JSON ARRAY (not an object) with one entry per question below:
       [{{"question": "<the exact question text>", "answer": "<tailored answer>", "confidence": <0.0-1.0>}}, ...]
       - QUESTIONS TO ANSWER:
{formatted_questions}
       - INSTRUCTIONS: Answer each question by identifying the most relevant experiences from the RESUME that demonstrate the skills or qualifications requested in the JOB DESCRIPTION. Ensure the tone is professional and the answers are concise (under 150 words each unless specified).
       - "confidence": your honest self-assessment (0.0-1.0) of how well-supported this answer is by the actual RESUME content. Use a LOW confidence (below 0.5) when you had to generalize, guess, or the resume has little directly relevant evidence for this question - don't inflate it. This number is used to decide whether a human should review the answer before it's submitted anywhere, so it must be a genuine estimate, not a default.
       - IMPORTANT: these answers get typed directly into real form fields on a real job application - never write meta-commentary about the resume (e.g. "Not specified on the resume", "The resume does not mention this"). If a question asks for a specific piece of data (a URL, a number, a date, a yes/no) that the RESUME simply doesn't contain, return "" (empty string) as the answer with confidence 0.0, rather than a sentence explaining its absence.
    """

    return f"""
    Act as an expert career coach. Analyze the provided RESUME and JOB DESCRIPTION.
    Generate a JSON object with the following keys:

    1. "company_name": The name of the company from the job description.
       - If not found, use "Company".

    2. "fit_score": A number from 0.0 to 1.0 representing how well this candidate's RESUME
       matches the JOB DESCRIPTION's requirements (skills, experience level, domain).
       - Be a realistic, discriminating judge - most candidates are NOT a 0.9+ fit. Reserve
         0.8+ for a strong, direct match on the core requirements; use 0.4-0.6 for a partial
         match with real gaps; use below 0.3 when core requirements are clearly unmet.
       - This score is used to decide whether to spend an application on this job at all,
         so err toward an honest, slightly conservative number rather than an optimistic one.

    {outreach_instructions}

    4. "cover_letter": A professional, 3-paragraph cover letter.
       - Paragraph 1: State position and enthusiasm.
       - Paragraph 2: Highlight 2-3 key experiences from resume aligning with JD.
       - Paragraph 3: Reiterate interest and call to action.
       - IMPORTANT: Do not make up facts. Omit missing details.

    {questions_instruction}

    Output must be valid JSON only. Do not wrap in markdown code blocks.

    --- JOB DESCRIPTION ---
    {jd_content}

    --- RESUME ---
    {resume_content}
    """

# --- Core Logic Function (Reusable) ---
def process_application(job_desc, resume, contact_name, contact_role, personal_note=None, fit_note=None, output_dir='output', message_type='role_specific', specific_hook=None, questions=None):
    """
    Core logic to process the application generation.
    Returns a dictionary with paths to generated files and content.
    """
    jd_content = fetch_job_description(job_desc)
    
    # Handle resume input...
    resume_content = ""
    try:
        if os.path.exists(resume):
             resume_content = pathlib.Path(resume).read_text()
        else:
             resume_content = resume
    except OSError:
        resume_content = resume

    print("🧠 Generating Application Packet (Single API Call)...")
    combined_prompt = create_combined_prompt(jd_content, resume_content, contact_name, contact_role, personal_note, fit_note, message_type, specific_hook, questions)
    response_text = generate_content(combined_prompt)
    
    # Parse JSON (handle potential markdown wrapping)
    try:
        # Strip ```json ... ``` if present
        clean_json = response_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
    except json.JSONDecodeError:
        # Fallback to simple split or just returning error if it fails badly
        # But let's try to survive partial failure
        print("⚠️ Failed to parse JSON response. Raw response:\n", response_text)
        return {
            "cover_letter_text": "Error generating content. Please try again.",
            "outreach_email_text": "",
            "question_answers": {},
            "question_answers_detailed": [],
            "fit_score": 0.0,
            "cover_letter_path": "",
            "outreach_email_path": None,
            "company_name": "Error"
        }

    company_name = data.get("company_name", "Company")
    cover_letter_text = data.get("cover_letter", "")
    outreach_email_text = data.get("outreach_email", "")
    fit_score = data.get("fit_score", 0.5)
    try:
        fit_score = max(0.0, min(1.0, float(fit_score)))
    except (TypeError, ValueError):
        fit_score = 0.5

    # question_answers now comes back from Gemini as a list of
    # {question, answer, confidence} objects (see create_combined_prompt).
    # Normalize defensively in case the model returns the old dict shape or
    # omits confidence, and also build a plain {question: answer} dict for
    # backward compatibility with the HTML template / older callers.
    raw_qa = data.get("question_answers", [])
    question_answers_detailed = []
    if isinstance(raw_qa, list):
        for item in raw_qa:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence", 0.5)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.5
            question_answers_detailed.append({
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "confidence": confidence,
            })
    elif isinstance(raw_qa, dict):
        # Defensive fallback if the model ignores the new format.
        for q, a in raw_qa.items():
            question_answers_detailed.append({"question": q, "answer": a, "confidence": 0.5})

    question_answers = {qa["question"]: qa["answer"] for qa in question_answers_detailed}

    # Sanitize filename
    safe_company_name = "".join([c for c in company_name if c.isalnum() or c in ('_', '-')]).strip()
    safe_company_name = safe_company_name[:50] 
    if not safe_company_name: safe_company_name = "Company"
        
    print(f"🏢 Identified Company: {safe_company_name}")
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
        "question_answers": question_answers,
        "question_answers_detailed": question_answers_detailed,
        "fit_score": fit_score,
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
@click.option('--message-type', default='role_specific', type=click.Choice(['role_specific', 'exploratory']), help='Type of outreach message.')
@click.option('--specific-hook', help='Specific research or hook for the outreach message.')
def create_packet(job_desc, resume, contact_name, contact_role, personal_note, fit_note, output_dir, message_type, specific_hook):
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
        process_application(job_desc, resume, contact_name, contact_role, personal_note, fit_note, output_dir, message_type, specific_hook)
        
        click.echo("✅ Successfully saved all files.")
        click.echo("\n🎉 Job application packet created successfully! 🎉")

    except Exception as e:
        click.echo(f"❌ Error: {e}")

   
   
if __name__ == '__main__':
       cli()
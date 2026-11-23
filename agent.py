import click
import os
import pathlib
import dotenv
from google import genai
from google.genai import types as genai_types
import requests
from bs4 import BeautifulSoup
import json
import re

# Load environment variables and configure API
dotenv.load_dotenv()

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Thinking budget for the packet (fit score + cover letter + answers) call. Measured Nov 2026:
# 512 gives fit scores as stable as the default at ~85% fewer thinking tokens; 0 caused a
# failed (0.0) score. Set PACKET_THINKING_BUDGET=-1 for the model default.
_ptb = os.getenv("PACKET_THINKING_BUDGET", "512")
PACKET_THINKING_BUDGET = int(_ptb) if _ptb.strip().isdigit() else None


class GeminiModel:
    """Thin adapter over the google-genai SDK (the deprecated google-generativeai
    package couldn't control "thinking"). Keeps the old call shape -
    generate_content(prompt, generation_config=dict) -> object with .text and
    .usage_metadata - so tests can swap in a FakeModel exactly as before.

    generation_config keys: response_mime_type, thinking_budget (0 = off).
    """

    def __init__(self, api_key, model_name):
        # JSON mode is only enabled on v1beta (v1 rejects response_mime_type).
        self.client = genai.Client(api_key=api_key, http_options=genai_types.HttpOptions(api_version="v1beta"))
        self.model_name = model_name

    def generate_content(self, prompt, generation_config=None):
        cfg = dict(generation_config or {})
        kwargs = {}
        if cfg.get("response_mime_type"):
            kwargs["response_mime_type"] = cfg["response_mime_type"]
        if cfg.get("thinking_budget") is not None:
            kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=int(cfg["thinking_budget"]))
        return self.client.models.generate_content(
            model=self.model_name, contents=prompt,
            config=genai_types.GenerateContentConfig(**kwargs) if kwargs else None)


try:
    model = GeminiModel(os.getenv("GEMINI_API_KEY"), MODEL_NAME) if os.getenv("GEMINI_API_KEY") else None
except Exception as e:
    print(f"Error configuring AI model: {e}")
    model = None

# --- Helper function to call the Gemini API ---
def generate_content(prompt, json_mode=False, thinking_budget=None):
    """Sends a prompt to the Gemini API and returns the response.

    json_mode=True sets response_mime_type to force Gemini to emit valid JSON
    directly, rather than relying on prompt instructions ("Output must be
    valid JSON only") that it can still deviate from - found via a real
    Applier-Engine run where json.loads() failed on a large/noisy scraped job
    description, silently degrading to the 0.0 fit_score error fallback in
    process_application() and causing a job to look like a genuine low-fit
    skip when it was actually an unparseable-response failure.
    """
    if not model:
        return "AI model is not configured. Please check your API key."
    try:
        generation_config = {}
        if json_mode:
            generation_config["response_mime_type"] = "application/json"
        if thinking_budget is not None:
            # Hidden "thinking" is billed as output and was ~70% of all tokens
            # (measured Nov 2026); structured tasks rarely need much of it.
            generation_config["thinking_budget"] = thinking_budget
        response = model.generate_content(prompt, generation_config=generation_config or None)
        _record_usage(response)
        return response.text
    except Exception as e:
        return f"An error occurred while generating content: {e}"


# --- Token accounting (returned to callers as "usage" so spend is visible) ---
# Gunicorn sync workers serve one request at a time, so a per-process counter
# reset at the start of each request is accurate.
USAGE = {"calls": 0, "inputTokens": 0, "outputTokens": 0, "thinkingTokens": 0}


def reset_usage():
    for k in USAGE:
        USAGE[k] = 0


def get_usage():
    return dict(USAGE)


def _record_usage(response):
    um = getattr(response, "usage_metadata", None)
    USAGE["calls"] += 1
    if um is None:
        return
    prompt = int(getattr(um, "prompt_token_count", 0) or 0)
    output = int(getattr(um, "candidates_token_count", 0) or 0)
    total = int(getattr(um, "total_token_count", 0) or 0)
    USAGE["inputTokens"] += prompt
    USAGE["outputTokens"] += output
    # google-generativeai 0.8.x doesn't expose thoughts_token_count; Gemini 2.5's
    # hidden "thinking" (billed as output) is whatever the total doesn't explain.
    USAGE["thinkingTokens"] += int(getattr(um, "thoughts_token_count", 0) or 0) or max(0, total - prompt - output)

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

# How every cover letter is written (packet API and web UI). The applicant asked for
# letters with a human touch that don't read as AI slop (Nov 2026) - keep these rules
# concrete; vague "sound natural" instructions don't change the output.
BANNED_PHRASES = [
    'I am writing to',
    'express my interest',
    'enthusiastic',
    'excited to apply',
    'thrilled',
    'eager',
    'passionate',
    'leverage',
    'utilize',
    'spearhead',
    'synergy',
    'dynamic',
    'fast-paced',
    'cutting-edge',
    'innovative solutions',
    'proven track record',
    'results-driven',
    'detail-oriented',
    'team player',
    'wealth of experience',
    'align perfectly',
    'perfect fit',
    'unique blend',
    'testament to',
    'delve',
    'navigate',
    'landscape',
    'realm',
    'seamless',
    'foster',
    'elevate',
    'make a meaningful impact',
    'I am confident',
    'I believe I would be',
    'valuable asset',
    'look forward to the opportunity',
    'Thank you for your time and consideration',
    "I'm writing",
    'robust',
    'aligns well',
    'well-prepared',
    'well-positioned',
    "contribute to the team's goals",
    'at your convenience',
    'significant',
    'diverse',
    'aligns with',
    'aligns closely',
    'I believe',
    'directly applicable',
    'could benefit',
]

_COVER_LETTER_STYLE = """
       Write it the way this candidate would write it themselves: an experienced engineer writing plainly, in first person, to the people they'd work with. It must not read like AI-generated text.
       - 170-250 words in 3 or 4 short paragraphs. Start with "Dear <Company> team," (or "Dear Hiring Manager," if the company is unknown). End with "Best," on its own line, then the candidate's full name exactly as it appears on the RESUME.
       - Open with something specific, never with who the candidate is or that they are applying (no "I'm writing", "I'm reaching out", "regarding the role"): one concrete thing from the JOB DESCRIPTION (the product, the problem this team owns, a stated requirement) tied to something the candidate actually did. Do not restate the job title in the first sentence.
       - Then 2 concrete examples from the RESUME closest to this job's main requirements: what the candidate did and what came of it, using the resume's own numbers, and in plain words how that relates to this team's work.
       - Don't summarize the fit in abstract terms ("aligns with", "combined with", "well-suited", "well-prepared", "my background could contribute"); the examples show it.
       - Close in one or two sentences with a plain ask to talk.
       - Sound like a person: vary sentence length, use contractions (I've, I'd, that's), prefer short common words, be direct and a little understated. Specific beats impressive.
       - Never use these words or phrases: {BANNED}.
       - No em dashes or en dashes (use commas, periods or a plain hyphen), no exclamation marks, no bullet points, headings, bold text, or placeholders like [Company].
       - Don't flatter the company or repeat its marketing copy back. Don't say where the posting was seen.
       - Do not make up facts: no invented years, tools, domains, employers, habits or numbers, not even softly ("I've often...", "I'm familiar with"). If the job wants something the RESUME doesn't show, leave it out entirely. Never mention the resume itself or what it does or doesn't contain.
"""
COVER_LETTER_STYLE = _COVER_LETTER_STYLE.replace("{BANNED}", ", ".join(f'"{p}"' for p in BANNED_PHRASES))


def tidy_cover_letter(text):
    """Backstop for the style rules the model most often ignores: dashes and stray markdown."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\s*[\u2014\u2013]\s*", " - ", text)
    text = text.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_META_RESUME = re.compile(r"\b(my|the) (resume|cv)\b", re.I)


def cover_letter_problems(text):
    """What a letter still does that the style rules forbid - Gemini follows a list of don'ts only loosely."""
    if not text:
        return []
    low = text.replace("\u2019", "'").lower()
    out = [f'"{p}"' for p in BANNED_PHRASES if p.lower() in low]
    if _META_RESUME.search(text):
        out.append('talks about "my resume" (talk about the work, never about the resume)')
    if "!" in text:
        out.append("exclamation mark")
    return out


def polish_cover_letter(text, resume_content, jd_content):
    """One targeted edit pass when the letter breaks the rules; keeps the original if the edit isn't better."""
    problems = cover_letter_problems(text)
    if not problems:
        return text
    prompt = f"""
    Edit this cover letter. Fix exactly these problems and change as little else as possible:
    - Remove or rephrase: {"; ".join(problems)}
    - Delete any sentence that claims experience, tools or habits the RESUME below doesn't show, and any sentence about what the resume does or doesn't contain.
    Keep the greeting, the sign-off, the real numbers from the resume, plain first person with contractions, 170-250 words.
    Never use: {", ".join(BANNED_PHRASES)}. No em dashes, no exclamation marks. Output only the edited letter.

    --- LETTER ---
    {text}

    --- JOB DESCRIPTION ---
    {jd_content[:6000]}

    --- RESUME ---
    {resume_content}
    """
    try:
        edited = tidy_cover_letter(generate_content(prompt, thinking_budget=PACKET_THINKING_BUDGET))
    except Exception as e:
        print(f"⚠️ Cover letter polish failed, keeping the draft: {e}")
        return text
    if len(edited) < 400 or len(cover_letter_problems(edited)) >= len(problems):
        return text
    return edited


# --- New: Helper functions to create specific prompts ---
def create_cover_letter_prompt(jd_content, resume_content):
    """Creates a detailed prompt for generating a cover letter."""
    return f"""
    Based on the provided resume and job description, write the candidate's cover letter for this job.
{COVER_LETTER_STYLE}
    Output only the letter text.

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
        # No specific contact: still draft a short, reusable connection note the
        # candidate can personalize and send by hand to a hiring manager or an
        # engineer on the team (referrals convert far better than cold applies).
        # Applier-Engine surfaces this in its daily summary; it is never sent
        # automatically.
        outreach_instructions = """
        2. "outreach_email": A LinkedIn connection-request note to a hiring manager or engineer on this team.
           - STRICT LIMIT: under 300 characters total.
           - Start with exactly "Hi [Name]," (literal placeholder - the candidate fills it in).
           - Name the specific role, and connect ONE concrete, real experience from the RESUME to the team's work.
           - Ask for a brief chat or whether they'd be open to referring - one short ask, no pressure.
           - TONE: warm, specific, human. No "I hope this finds you well", no flattery, no made-up facts.
        """

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

    4. "cover_letter": The candidate's cover letter for this job, as plain text with blank lines between paragraphs.
{COVER_LETTER_STYLE}

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

    # json_mode=True (response_mime_type) makes a valid-JSON response far more
    # likely but not guaranteed - a large/noisy job description (e.g. a full
    # scraped page rather than just the JD) can still occasionally produce an
    # unparseable response. Retrying once resolves most of these, confirmed
    # against a real request that failed 3 times in a row on one call but
    # succeeded on a fresh call with identical input - this is intermittent,
    # not deterministic, so a retry is the right fix rather than assuming the
    # first failure means a genuine 0 fit.
    data = None
    response_text = ""
    for attempt in range(2):
        response_text = generate_content(combined_prompt, json_mode=True, thinking_budget=PACKET_THINKING_BUDGET)
        try:
            clean_json = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            break
        except json.JSONDecodeError:
            if attempt == 0:
                print("⚠️ Failed to parse JSON response, retrying once...")
            continue

    if data is None:
        print("⚠️ Failed to parse JSON response after retry. Raw response:\n", response_text)
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
    cover_letter_text = polish_cover_letter(tidy_cover_letter(data.get("cover_letter", "")), resume_content, jd_content)
    outreach_email_text = data.get("outreach_email", "")
    if not isinstance(outreach_email_text, str):
        # Model occasionally returns null or a nested object; never crash the packet over it.
        outreach_email_text = "" if outreach_email_text is None else json.dumps(outreach_email_text)
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
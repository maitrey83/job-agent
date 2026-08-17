# Job Application Agent Documentation

## 0. Programmatic API (used by Applier-Engine)

In addition to the web UI and CLI below, `app.py` exposes
`POST /api/generate-packet` for other programs to call directly - this is
how [Applier-Engine](https://github.com/maitrey83) (the Playwright form-filler)
gets tailored content. Request/response shape:

```
POST /api/generate-packet
{
  "jobDescription": "...",        // required
  "resumeText": "...",            // required
  "unmappedQuestions": ["..."]    // optional - labels/questions to answer
}
->
{
  "companyName": "...",
  "coverLetter": "...",
  "fitScore": 0.0-1.0,
  "questionAnswers": [{"question": "...", "answer": "...", "confidence": 0.0-1.0}]
}
```

If `APPLIER_API_KEY` is set in the environment, callers must send it back as
the `X-API-Key` header or the request is rejected with 401. Set this once
this is deployed publicly - an open endpoint spends your Gemini quota on
whoever finds the URL.

Gemini's `fit_score` and per-answer `confidence` are self-reported estimates
(see the prompt in `create_combined_prompt` in `agent.py`) - they're what
Applier-Engine uses to decide whether to bother applying at all, and whether
to trust an answer enough to submit without a human looking at it first.

## 1. Overview

The Job Application Agent is a command-line interface (CLI) tool designed to streamline the job application process. It leverages the Google Gemini AI model to automatically generate professional and tailored job application materials.

Given a job description and a base resume, the agent can produce:
- A compelling cover letter.
- A concise outreach email for networking or referral requests.

This tool is built with Python and uses the `click` library for its command-line interface.

## 2. Project Structure

The project is organized into the following key files and directories:

```
/
├─── .env
├─── agent.py
├─── requirements.txt
├─── input/
│    ├─── jd.txt
│    └─── resume.txt
└─── output/
     ├─── cover_letter.txt
     └─── outreach_email.txt
```

- **`agent.py`**: The core script containing all the application logic, including the CLI commands, API interaction, and file processing.
- **`requirements.txt`**: A list of all Python dependencies required to run the agent.
- **`.env`**: A configuration file used to store sensitive information, specifically the `GEMINI_API_KEY`.
- **`input/`**: The directory where user-provided files are stored.
  - **`jd.txt`**: A plain text file containing the job description.
  - **`resume.txt`**: A plain text file containing the candidate's resume.
- **`output/`**: The directory where the agent saves the generated files.
  - **`cover_letter.txt`**: The generated cover letter.
  - **`outreach_email.txt`**: The generated outreach email.

## 3. Setup and Installation

To get the agent up and running, follow these steps:

### Step 1: Install Dependencies

Ensure you have Python installed. Then, install the required libraries using pip and the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

### Step 2: Configure API Key

The agent requires an API key for the Google Gemini service to function.

1.  Create a file named `.env` in the project's root directory.
2.  Add your API key to the file in the following format:

    ```
    GEMINI_API_KEY="YOUR_API_KEY_HERE"
    ```

## 4. Usage

The agent is operated from the command line. The main command is `create-packet`, which generates the complete set of application documents.

### Command

```bash
python agent.py create-packet [OPTIONS]
```

### Options

The `create-packet` command accepts the following options:

-   `--job-desc`, `-j`: **(Required)** The path to the job description file.
-   `--resume`, `-r`: **(Required)** The path to the resume file.
-   `--contact-name`, `-n`: (Optional) The name of the person you are emailing (e.g., "Jane Doe").
-   `--contact-role`, `-c`: (Optional) The role of the contact (e.g., "Hiring Manager").
-   `--output-dir`, `-o`: (Optional) The directory where output files will be saved. Defaults to `output/`.

### Example

To generate a cover letter and an outreach email for a contact named "Jane Doe":

```bash
python agent.py create-packet \
    --job-desc input/jd.txt \
    --resume input/resume.txt \
    --contact-name "Jane Doe" \
    --contact-role "Recruiter"
```

The agent will read the files, generate the content, and save `cover_letter.txt` and `outreach_email.txt` in the `output/` directory.

## 5. Code Details (`agent.py`)

The script is structured into several key parts:

### a. Initialization

-   **Imports**: Imports necessary libraries like `click`, `os`, `pathlib`, `dotenv`, and `google.generativeai`.
-   **Environment Loading**: Uses `dotenv.load_dotenv()` to load the `GEMINI_API_KEY` from the `.env` file.
-   **AI Model Configuration**: Initializes the `GenerativeModel` with the specified model name (`gemini-2.5-flash`) and the API key.

### b. Helper Functions

-   **`generate_content(prompt)`**: This function is the bridge to the Gemini API. It takes a text prompt, sends it to the model, and returns the generated text response. It includes error handling for API-related issues.
-   **`create_cover_letter_prompt(...)`**: Constructs a detailed, structured prompt for generating a cover letter. It combines the job description and resume with specific instructions on the desired format and tone.
-   **`create_outreach_email_prompt(...)`**: Constructs a prompt for generating a professional outreach email, incorporating the contact's name and role for a personalized touch.

### c. CLI Commands (Click Interface)

-   **`@click.group()`**: Defines the main command group `cli`.
-   **`@cli.command()`**: Defines the `create-packet` command.
-   **`@click.option(...)`**: Defines the command-line options described in the "Usage" section.
-   **`create_packet(...)` function**: This is the main function that executes when the command is run. It performs the following steps:
    1.  Reads the content from the specified job description and resume files.
    2.  Calls `create_cover_letter_prompt` to build the cover letter prompt.
    3.  Calls `generate_content` to get the cover letter text from the AI.
    4.  If a contact name and role are provided, it repeats the process for the outreach email.
    5.  Creates the output directory if it doesn't exist.
    6.  Saves the generated text into the respective files in the output directory.
    7.  Prints status messages to the console to keep the user informed of its progress.

import os
import sys
from agent import process_application
import json

# Minimal mock data
MOCK_JD = """
Software Engineer Position
Requirements: 5+ years of experience with Python, experience with Flask, knowledge of SQL.
Responsibilities: Building scalable web applications, collaborating with cross-functional teams.
"""

MOCK_RESUME = """
John Doe
555-0199 | john.doe@email.com
Experience:
Senior Software Engineer at Tech Corp (4 years)
- Developed Python based microservices.
- Lead a team to build a Flask application.
Software Engineer at Dev Inc (2 years)
- Used SQL to optimize database queries.
"""

QUESTIONS = [
    "What interest you in the job?",
    "How does your experience with Python align with our requirements?",
    "Tell us about your experience with Flask."
]

def test_questions():
    print("Testing process_application with questions...")
    try:
        results = process_application(
            job_desc=MOCK_JD,
            resume=MOCK_RESUME,
            contact_name="Jane Smith",
            contact_role="Hiring Manager",
            questions=QUESTIONS
        )
        
        print("\n--- RESULTS ---")
        print(f"Company Name: {results['company_name']}")
        print(f"Question Answers: {json.dumps(results['question_answers'], indent=2)}")
        
        if results['question_answers'] and len(results['question_answers']) == len(QUESTIONS):
            print("\n✅ Verification SUCCESS: All questions answered.")
        else:
            print("\n❌ Verification FAILED: Some questions missing answers.")

    except Exception as e:
        print(f"\n❌ Error during verification: {e}")

if __name__ == "__main__":
    test_questions()

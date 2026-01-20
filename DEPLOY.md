# Cloud Deployment Guide (Render.com)

This app is now "cloud-ready"! You can deploy it for free on [Render.com](https://render.com), which is one of the easiest ways to host Python apps.

## Prerequisites
1.  A GitHub account.
2.  This code pushed to a GitHub repository (I've already initialized git for you).

## Step 1: Push Code to GitHub
(Run these in your terminal if you haven't already connected a remote repo)
1.  Create a new repository on GitHub (name it `job-agent`).
2.  Run:
    ```bash
    git remote add origin https://github.com/YOUR_USERNAME/job-agent.git
    git push -u origin main
    ```

## Step 2: Deploy on Render
1.  Go to [dashboard.render.com](https://dashboard.render.com) and sign up/login.
2.  Click **New +** -> **Web Service**.
3.  Connect your GitHub account and select your `job-agent` repository.
4.  **Configure the Service**:
    -   **Name**: `job-agent` (or whatever you like)
    -   **Runtime**: `Python 3`
    -   **Build Command**: `pip install -r requirements.txt`
    -   **Start Command**: `gunicorn app:app` (This should auto-fill thanks to the Procfile).
    -   **Instance Type**: `Free`
5.  **Environment Variables** (Crucial!):
    -   Scroll down to "Environment Variables".
    -   Add `GEMINI_API_KEY`: Paste your actual API key here (from your `.env` file).
    -   (Optional) `RESUME_PATH`: You can ignore this if you mainly paste/upload resumes.
6.  Click **Create Web Service**.

## That's it!
Render will build your app and give you a URL (e.g., `https://job-agent.onrender.com`). You can access this URL from anywhere, anytime.

**Note on Free Tier**: The free instance on Render "spins down" after inactivity. The first request after a while might take 30-50 seconds to load. This is normal.

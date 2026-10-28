# Run Ad Doctor on your computer

Install Python 3.12 (including pip), then extract this project to a folder.

- Windows: double-click `start_ad_doctor.bat`.
- Mac/Linux: open a terminal in this folder and run `bash start_ad_doctor.sh`.

Keep the terminal open. The first start downloads dependencies. Open
`http://localhost:8501` in your browser once Streamlit has started.

This address works on the computer running the launcher. It is not a public URL.

Your local SQLite database is created at `.venv/development.db`; the bundled
production database and credentials are deliberately excluded from the download.
Start with **Manage sources** to import your approved source spreadsheet.
The interface also provides topic qualification, Content Studio, human review,
publishing, subscribers, schedules, logs and provider settings. Empty pages are
expected until you add sources and run the corresponding workflow stages.

Automatic schedules are disabled in local mode. No API credentials are included;
Perplexity's free website requires manual use as described in
`ad_doctor/FREE_RESEARCH_WORKFLOW.md`. Live research, Google Docs and email require
separate configuration and have not been validated here.

The Windows launcher was inspected but could not be executed in the Linux
workspace. The Linux launcher's shell syntax, application rendering and dashboard
navigation were checked; internet access to PyPI is required for installation.

"""Copy to `firebase_embedded.py` and fill from Firebase Console → Project settings → General."""

from __future__ import annotations

# Web API Key (Project settings → General → Your apps → Web app)
FIREBASE_WEB_API_KEY = ""

# Project ID (same page)
FIREBASE_PROJECT_ID = ""

# Storage bucket name only, e.g. my-project.appspot.com or my-project.firebasestorage.app
FIREBASE_STORAGE_BUCKET = ""

# Google Cloud → APIs & Services → Credentials → OAuth client ID → Application type: Desktop
# Required for "Continue with Google" and "Link Google account" (same GCP project as Firebase).
GOOGLE_OAUTH_CLIENT_ID = ""
GOOGLE_OAUTH_CLIENT_SECRET = ""

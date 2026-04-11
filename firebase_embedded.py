"""Shipped Firebase client identity (same idea as Firebase web `firebaseConfig` in JS).

The Web API key is a client identifier, not a secret—access is enforced by Firebase Auth,
Storage rules, and API key restrictions in Google Cloud. Environment variables still override
these values when set (for local testing or alternate projects).
"""

from __future__ import annotations

# RootRecord production project — from Firebase Console → Project settings → General (Web app).
FIREBASE_WEB_API_KEY = "AIzaSyC9q9x9Xh8wgj9FRa-DflAG3qUvF4x6dz4"
FIREBASE_PROJECT_ID = "root-record"
FIREBASE_STORAGE_BUCKET = "root-record.firebasestorage.app"

# Google Sign-In (OAuth 2.0 “Desktop” client — Google Cloud → APIs & Services → Credentials).
# Enable Google in Firebase → Authentication → Sign-in method, then create an OAuth client ID
# of type “Desktop” in the same GCP project and paste ID + secret here.
GOOGLE_OAUTH_CLIENT_ID = "105847061623-r28136cmt8ks5v92j5m460sj958kgdgm.apps.googleusercontent.com"
GOOGLE_OAUTH_CLIENT_SECRET = "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"

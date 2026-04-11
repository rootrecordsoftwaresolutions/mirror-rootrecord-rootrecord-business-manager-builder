# Firebase setup (manual — Google Cloud Console)

## How production installs work

**Shipped builds include Firebase client settings** in `firebase_embedded.py` at the project root (same role as `firebaseConfig` in a web or mobile app). That file is **bundled inside `RootRecord.exe`**. New users only need **email + password** to sign up or sign in—they never paste API keys or edit `.env`.

The steps below are for **you** (the vendor) when creating or changing the Firebase project, or for **developers** overriding config locally. Customers do not do this after installing.

---

Do this once per environment when you set up or change the backend.

## 1. Create project

1. Open [Firebase Console](https://console.firebase.google.com/) → **Add project**.
2. Enable **Google Analytics** only if you want it (optional).

## 2. Authentication (user logins)

End-user accounts are **Firebase Authentication** users. In the desktop app: **My Account → Sign in**, **Create account**, or **Continue with Google** / **Sign up with Google**.

1. **Build → Authentication → Sign-in method**.
2. Enable **Email/Password** (Email link is optional).
3. Enable **Google** and set a support email (required by Google). This allows **Sign in with Google** in the app.

### Google Sign-In (desktop OAuth)

The app uses a **Google Cloud OAuth 2.0 “Desktop”** client (browser + localhost callback), then Firebase’s `signInWithIdp` API.

1. In [Google Cloud Console](https://console.cloud.google.com/) (same project as Firebase): **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Desktop**.
3. Copy the **Client ID** and **Client secret** into `firebase_embedded.py` as `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` (or set the same names as environment variables).

**Sign-in screen:** **Continue with Google** / **Sign up with Google** always appear once Firebase is configured. If the OAuth client ID and secret are missing, tapping the button shows instructions instead of opening the browser. Email/password still works without Google.

New users appear under **Authentication → Users** in the Firebase console.

## 3. Storage

1. **Build → Storage** → Get started → use default rules for development, then replace with the rules below.

### Storage security rules (production-style)

In **Storage → Rules**, use rules that only allow each auth user to access their own prefix:

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /users/{userId}/{allPaths=**} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
  }
}
```

Publish rules. Unauthenticated uploads are denied.

## 4. Web API key (desktop app)

1. **Project settings** (gear) → **General** → **Your apps** → add a **Web** app if needed.
2. Copy **Web API key**, **Project ID**, and (if needed) **Storage bucket** into **`firebase_embedded.py`** at the project root (same fields as the Firebase JS `firebaseConfig`). Copy **`firebase_embedded.example.py`** → **`firebase_embedded.py`** if the file is missing.
3. **Optional:** override with **environment variables** or a **`.env`** file (see §7); env wins over `firebase_embedded.py`.

## 5. Storage bucket name

Set **`FIREBASE_STORAGE_BUCKET`** in **`firebase_embedded.py`** (or `.env`) to the bucket **name only** (no `https://`, no `gs://`).

- Legacy default buckets often look like **`your-project-id.appspot.com`**.
- Newer projects may show **`your-project-id.firebasestorage.app`** in **Storage → Files** (bucket selector). That full hostname is the value to use, e.g. `root-record.firebasestorage.app`.

If you omit `FIREBASE_STORAGE_BUCKET`, the app falls back to **`{FIREBASE_PROJECT_ID}.appspot.com`**. If your project only uses a `.firebasestorage.app` bucket, **set `FIREBASE_STORAGE_BUCKET` explicitly**.

The desktop app **tries multiple bucket names** in order (your configured value, then `{project}.appspot.com`, then `{project}.firebasestorage.app`) when an upload returns **404**, because Firebase sometimes shows one hostname in the console while the Storage API expects the other.

### If backup fails

- **404 / Not Found**: Open **Storage → Files** and copy the **exact** bucket name from the bucket dropdown into `FIREBASE_STORAGE_BUCKET`. Ensure **Storage** is enabled for the project. The app will retry alternate bucket hostnames automatically; if all fail, the bucket ID does not match your project.
- **403 / permission denied**: Check **Storage rules** (section 3) and that you are **signed in** under **My Account** so uploads go to `users/{uid}/...`.
- **401**: Sign out and sign in again (refresh token expired).
- **API key**: Ensure **Identity Toolkit API** is enabled for the project and `FIREBASE_WEB_API_KEY` matches the **Web** app key in Project settings.

## 6. Restrict API key (recommended)

In [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials** → edit the browser key Firebase created:

- **API restrictions**: Restrict key → enable **Identity Toolkit API**, **Token Service API**, and **Cloud Storage** (or Firebase-related APIs your project uses).
- **Application restrictions**: For a desktop exe, full restriction is limited; prefer monitoring usage and rotating keys if abused.

## 7. Overrides (optional `.env` / env vars)

**Default:** the app reads **`firebase_embedded.py`** (Web API key, project id, storage bucket)—the same information Firebase shows for a **Web** app client. Users are **not** asked to paste keys at runtime.

**Override order (highest wins):** `FIREBASE_*` **environment variables**, then **`.env` files** loaded by `env_loader` (see `env_loader.py`), then **`firebase_embedded.py`**.

For **installed** builds, `.env` can live beside `RootRecord.exe`, under `%LOCALAPPDATA%\RootRecord\.env`, or `%ROOTRECORD_HOME%\.env`. **Never commit** real `.env` or private forks of `firebase_embedded.py` to a public repo; rotate keys if exposed.

After this, the RootRecord **My Account** tab can sign in and run **Backup database to cloud**. The **local SQLite file** remains the primary data store; cloud backup is an encrypted snapshot only.

## 8. Link Google to an email/password account

The desktop app calls Identity Toolkit `accounts:signInWithIdp` with your current Firebase `idToken` plus a Google ID token to **link** Google to the same Firebase user.

1. Sign in with **email and password** first.
2. Open **Account Settings** → **Link Google account** and finish Google OAuth in the browser.
3. The Google account email should match the Firebase account email unless your project allows otherwise.

If **Continue with Google** fails with “email already exists”, that email already has a Firebase account: sign in with **password** first, then use **Link Google account** (do not create a second user with Google alone).

**OAuth client:** Must be type **Desktop app** in the **same** Google Cloud project linked to Firebase. The app uses loopback (`http://127.0.0.1:<port>/`); Desktop clients allow this without manually listing every port.

## 9. Automatic cloud backup (desktop)

In **Account Settings**, enable:

- **Automatic cloud backup** — uploads on a timer (default ~45 minutes) while the app is open.
- **Sync when closing** — runs one upload before exit (best-effort).

Ensure **Storage** rules (section 3) and `FIREBASE_STORAGE_BUCKET` (section 5) are correct so uploads succeed; otherwise the app may fall back to Firestore metadata only.

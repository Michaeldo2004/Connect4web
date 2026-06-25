# Setup

## Requirements

- Node.js
- npm
- Python 3.12 recommended

The frontend runs on:

```text
http://localhost:5173
```

The backend runs on:

```text
http://localhost:5000
```

## Backend

From the project root:

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python backend\app.py
```

If `python` is not on PATH, use the installed Python executable directly:

```powershell
& 'C:\Users\micha\AppData\Local\Programs\Python\Python312\python.exe' -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python backend\app.py
```

## Frontend

From the frontend folder:

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm install
npm run dev
```

Vite opens the browser at `http://localhost:5173`.

## Build Check

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm run build
```


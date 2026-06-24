import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_document(file_path, file_name):
    with open(file_path, "rb") as f:
        supabase.storage.from_("IA pour etudiants").upload(file_name, f)
    print(f"{file_name} uploadé avec succès")

def list_documents():
    files = supabase.storage.from_("IA pour etudiants").list()
    return [f["name"] for f in files]

def delete_document(file_name):
    supabase.storage.from_("IA pour etudiants").remove([file_name])
    print(f"{file_name} supprimé")

def get_document_url(file_name):
    url = supabase.storage.from_("IA pour etudiants").get_public_url(file_name)
    return url
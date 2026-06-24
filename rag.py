import os
from dotenv import load_dotenv
from supabase import create_client
import openai
import PyPDF2
def indexer_depuis_supabase(nom_fichier):
    print(f"Téléchargement de {nom_fichier} depuis Supabase...")
    
    response = supabase.storage.from_("IA pour etudiants").download(nom_fichier)
    
    chemin_temp = f"temp_{nom_fichier}"
    with open(chemin_temp, "wb") as f:
        f.write(response)
    
    indexer_document(chemin_temp, nom_fichier)
    
    os.remove(chemin_temp)
    print(f"Fichier temporaire supprimé.")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

client = openai.OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

def extraire_texte_pdf(chemin_pdf):
    texte = ""
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            texte += page.extract_text() + "\n"
    texte = texte.replace("\x00", "")
    return texte

def decouper_texte(texte, taille=500):
    mots = texte.split()
    morceaux = []
    for i in range(0, len(mots), taille):
        morceau = " ".join(mots[i:i+taille])
        morceaux.append(morceau)
    return morceaux

def creer_embedding(texte):
    response = client.embeddings.create(
        model="text-embedding-ada-002",
        input=texte
    )
    return response.data[0].embedding

def indexer_document(chemin_pdf, nom_fichier):
    print(f"Lecture de {nom_fichier}...")
    texte = extraire_texte_pdf(chemin_pdf)
    morceaux = decouper_texte(texte)
    
    print(f"Indexation de {len(morceaux)} morceaux...")
    for morceau in morceaux:
        embedding = creer_embedding(morceau)
        supabase.table("documents").insert({
            "nom": nom_fichier,
            "contenu": morceau,
            "embedding": embedding
        }).execute()
    
    print(f"{nom_fichier} indexé avec succès !")

def rechercher_documents(question, limite=3):
    embedding_question = creer_embedding(question)
    
    result = supabase.rpc("recherche_documents", {
        "query_embedding": embedding_question,
        "match_count": limite
    }).execute()
    
    return [r["contenu"] for r in result.data]
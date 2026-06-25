import os
from notion_client import Client
from rag import rechercher_documents

notion = Client(auth=os.getenv("NOTION_TOKEN"))

def get_liste_prompts():
    # Retourne la liste des pages Notion disponibles comme prompts
    results = notion.search(filter={"property": "object", "value": "page"}).get("results", [])
    prompts = []
    for page in results:
        try:
            titre = page["properties"]["title"]["title"][0]["plain_text"]
            prompts.append({"id": page["id"], "nom": titre})
        except:
            pass
    return prompts

def get_contenu_prompt(page_id):
    # Récupère le contenu complet d'une page Notion
    blocks = notion.blocks.children.list(block_id=page_id).get("results", [])
    contenu = ""
    for block in blocks:
        type_block = block["type"]
        try:
            texte = block[type_block]["rich_text"][0]["plain_text"]
            contenu += texte + "\n"
        except:
            pass
    return contenu

def recuperer_ressources(decision_router):
    contexte_final = ""

    # Récupérer les prompts choisis depuis Notion
    if decision_router.get("prompts"):
        liste_prompts = get_liste_prompts()
        for nom_prompt in decision_router["prompts"]:
            for p in liste_prompts:
                if nom_prompt.lower() in p["nom"].lower():
                    contenu = get_contenu_prompt(p["id"])
                    contexte_final += f"\n--- {p['nom']} ---\n{contenu}\n"

    # Récupérer les PDFs depuis Supabase via RAG
    if decision_router.get("pdfs"):
        for nom_pdf in decision_router["pdfs"]:
            chunks = rechercher_documents(nom_pdf)
            contexte_final += "\n--- Documents ---\n"
            contexte_final += "\n".join(chunks)

    return contexte_final
from tavily import TavilyClient
import os

def recherche_web(question: str) -> str:
    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    
    resultats = client.search(question, max_results=3)
    
    # Formater les résultats pour le LLM
    contexte = ""
    for r in resultats["results"]:
        contexte += f"- {r['title']}\n{r['content']}\n\n"
    
    return contexte
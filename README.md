# Appli web — Production RTE par groupe ⚡

Application web qui affiche la **production d'électricité par groupe de production**
en France, à partir de l'API officielle **« Actual Generation »** de RTE.

Interface : choix de la période, filtre par filière, graphique, tableau et export CSV.
Tes amis y accèdent via une simple **URL**, sans rien installer.

---

## Comment ça marche (en bref)

L'appli est écrite en **Python avec Streamlit**. Elle tourne sur un **serveur**
(gratuit) : c'est lui qui parle à l'API RTE avec ton mot de passe secret, qui
reste donc caché. Le navigateur de tes amis n'affiche que le résultat.

---

## Étape 1 — Obtenir tes identifiants RTE (gratuit, une fois)

1. Crée un compte sur https://data.rte-france.com
2. Abonne-toi à l'API **« Actual Generation »**
3. Dans **Mes applications**, crée une application **Web / Serveur**
4. Note le **`client_id`** et le **`client_secret`**

## Étape 2 — Mettre le code sur GitHub

Le plus simple sans ligne de commande :

1. Crée un compte sur https://github.com
2. Clique sur **New repository**, donne-lui un nom (ex. `rte-app`), garde-le public, valide
3. Sur la page du repo, clique **« uploading an existing file »** et
   glisse-dépose `app.py`, `requirements.txt` et le dossier `.streamlit`
   (⚠️ **sans** le fichier de secrets), puis **Commit changes**

> Variante en ligne de commande :
> ```bash
> git init && git add . && git commit -m "Appli RTE"
> git remote add origin https://github.com/TON_PSEUDO/rte-app.git
> git push -u origin main
> ```

## Étape 3 — Déployer gratuitement sur Streamlit Cloud

1. Va sur https://share.streamlit.io et connecte-toi avec ton compte GitHub
2. **Create app** → sélectionne ton repo, branche `main`, fichier `app.py`
3. Avant de déployer, ouvre **Advanced settings → Secrets** et colle :
   ```toml
   RTE_CLIENT_ID = "ton_client_id"
   RTE_CLIENT_SECRET = "ton_client_secret"
   ```
4. **Deploy**. Au bout d'une minute tu obtiens une URL publique du type
   `https://ton-appli.streamlit.app` — c'est le lien à partager. 🎉

---

## Tester en local (optionnel)

```bash
pip install -r requirements.txt
# copie .streamlit/secrets.toml.example en .streamlit/secrets.toml et remplis-le
streamlit run app.py
```

---

## Sécurité — à retenir

- **Ne mets jamais** `client_id` / `client_secret` dans `app.py` ni sur GitHub.
- Ils vont **uniquement** dans les *Secrets* de Streamlit Cloud (ou dans
  `.streamlit/secrets.toml` en local, ignoré par `.gitignore`).

## Notes données

- Disponibles à partir du **15/12/2014**.
- Valeurs en **MW**, production **nette injectée** : une valeur négative est
  normale pour un groupe à l'arrêt qui consomme ses auxiliaires.

## Licence des données

© RTE — soumises aux Conditions Générales d'Utilisation du portail RTE Data.

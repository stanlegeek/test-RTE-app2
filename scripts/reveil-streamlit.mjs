// Réveille l'application Streamlit et vérifie qu'elle est réellement debout.
//
// Pourquoi un navigateur et pas un simple curl : share.streamlit.io répond
// toujours 200 avec une coquille HTML vide (<div id="root"></div>), que
// l'application dorme ou non. Le compteur d'inactivité de Streamlit n'est
// remis à zéro que par une vraie session websocket, ouverte par le
// JavaScript de la page. La version curl de ce keep-alive était donc verte
// tous les jours pendant que l'app dormait.
//
// Lancé par .github/workflows/keep-alive.yml, mais utilisable à la main :
//   npm install playwright && npx playwright install chromium
//   node scripts/reveil-streamlit.mjs

import { chromium } from "playwright";

const url =
  process.env.APP_URL ??
  "https://test-rte-app2-yzbnnlgxqvqqq5kl34ufnr.streamlit.app";

// Titre d'onglet posé par st.set_page_config() dans app.py. Il n'apparaît que
// si le script Python a vraiment tourné, donc si l'app est debout.
const titreAttendu = process.env.TITRE_ATTENDU ?? "Production RTE par groupe";

const navigateur = await chromium.launch();
const page = await navigateur.newPage();

let codeSortie = 0;
try {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });

  // Si l'app dort, Streamlit affiche un bouton de réveil. Sinon ce bouton
  // n'apparaît jamais et on part directement en attente.
  const bouton = page.getByRole("button", { name: /get this app back up/i });
  try {
    await bouton.waitFor({ state: "visible", timeout: 20_000 });
    console.log("L'application dormait : clic sur le bouton de réveil.");
    await bouton.click();
  } catch {
    console.log("Pas de bouton de réveil : l'application était déjà debout.");
  }

  // Le conteneur peut mettre une bonne minute à redémarrer et à réinstaller
  // ses dépendances.
  await page.waitForFunction(
    (attendu) => document.title.includes(attendu),
    titreAttendu,
    { timeout: 300_000 },
  );

  // On laisse la session websocket vivre un peu, pour être sûr qu'elle est
  // bien comptée comme du trafic.
  await page.waitForTimeout(10_000);
  console.log(`Application debout, titre : "${await page.title()}"`);
} catch (erreur) {
  codeSortie = 1;
  console.log(`::error::L'application n'a pas répondu : ${erreur.message}`);
  console.log(`Titre vu : "${await page.title().catch(() => "?")}"`);
  await page.screenshot({ path: "echec.png", fullPage: true }).catch(() => {});
} finally {
  await navigateur.close();
}

process.exit(codeSortie);

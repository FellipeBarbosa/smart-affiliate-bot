import io
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime
from io import BytesIO

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import pandas as pd
from PIL import Image
import pyperclip
import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import win32clipboard

# --- CONFIGURACOES INICIAIS ---
load_dotenv()

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SIMULAR_DIGITACAO = True
DELAY_MIN_ENTRE_MENSAGENS = 5
DELAY_MAX_ENTRE_MENSAGENS = 15
ARQUIVO_HISTORICO = os.getenv("ARQUIVO_HISTORICO", "historico_ofertas.csv")
ARQUIVO_CACHE_ENVIOS = os.getenv("ARQUIVO_CACHE_ENVIOS", "cache_envios_5dias.json")
CACHE_RETENCAO_SECONDS = int(os.getenv("CACHE_RETENCAO_SECONDS", "432000"))
WA_CLICK_TIMEOUT = int(os.getenv("WA_CLICK_TIMEOUT", "12"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GRUPOS_ALVO = [
    "Ofertas Tech & Games",
]

# --- REGRAS DE QUALIDADE E FILTRO DE MARCAS (PADRAO TECH/JOTAPE) ---
PRECO_MINIMO_TECH = 65.0  # Bloqueia mouses de R$ 19 e itens descartaveis

MARCAS_TECH_APROVADAS = [
    # Perifericos & Audio
    "logitech", "razer", "hyperx", "redragon", "steelseries", "corsair", 
    "attack shark", "vgn", "machenike", "epomaker", "aula", "havit",
    "scyrox", "ajazz", "darmoshark", "fantech", "edifier", "jbl", "sennheiser",
    
    # Hardware & Componentes
    "asus", "gigabyte", "msi", "asrock", "galax", "zotac", "pny",
    "amd", "ryzen", "intel", "nvidia", "rtx", "geforce", "radeon",
    "kingston", "fury", "xpg", "adata", "crucial", "samsung", "deepcool",
    "thermalright", "nzxt", "cooler master", "super flower", "xpg core",
    
    # Monitores & Telas
    "ultragear", "aoc", "hero", "alienware", "benq", "zowie", "odyssey",
    
    # Consoles, Laptops & Games
    "playstation", "sony", "dualsense", "ps5", "xbox", "nintendo", "switch",
    "lenovo loq", "legion", "nitro", "tuf gaming", "rog", "predator", "avell"
]

TERMOS_BLOQUEADOS = [
    "usado", "recondicionado", "vitrine", "reparo", "peça de reposição", "peças para",
    "display lcd", "tela touch", "capinha", "película", "genérico", "paralelo",
    "controle remoto", "cartucho", "toner", "tinta para impressora", "refil de tinta",
    "pilha", "carregador de parede", "adaptador usb", "hub usb 2.0", "cabo vga",
    "freezer", "caminhão", "pneu", "calota", "chocadeira", "incubadora",
    "lola cosmetics", "shampoo", "condicionador", "sérum", "máscara", "batom",
    "perfume", "hidratante", "esmaltes", "base", "maquiagem"
]


def produto_eh_marca_confiavel(titulo):
    if not titulo:
        return False
    t_lower = titulo.lower()
    return any(marca in t_lower for marca in MARCAS_TECH_APROVADAS)


def produto_eh_bloqueado(titulo):
    if not titulo:
        return False
    titulo_min = titulo.lower()
    if any(termo.lower() in titulo_min for termo in TERMOS_BLOQUEADOS):
        return True
    return False


# --- MENSAGENS E TELEGRAM ---
def enviar_mensagem_telegram_seguro(texto):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "None":
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "HTML",
        }
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def enviar_telegram(mensagem_formatada, url_link, image_url=None):
    time.sleep(1)
    if image_url:
        params_photo = {
            "chat_id": TELEGRAM_CHAT_ID,
            "parse_mode": "HTML",
            "caption": mensagem_formatada,
        }
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resposta_img = requests.get(image_url, headers=headers, timeout=10)
            resposta_img.raise_for_status()
            files = {"photo": ("item.png", io.BytesIO(resposta_img.content))}
            url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            r = requests.post(url_api, data=params_photo, files=files, timeout=15)
            r.raise_for_status()
            return
        except Exception:
            pass

    try:
        params_fallback = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensagem_formatada,
            "parse_mode": "HTML",
        }
        url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url_api, data=params_fallback, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERRO] Envio Telegram: {e}")


# --- UTILITARIOS WHATSAPP ---
def human_delay(min_s=1, max_s=3):
    time.sleep(random.uniform(min_s, max_s))


def formatar_para_whatsapp(texto_html):
    if not texto_html:
        return ""
    mapa_tags = {
        "<b>": "*", "</b>": "*", "<strong>": "*", "</strong>": "*",
        "<i>": "_", "</i>": "_", "<em>": "_", "</em>": "_",
        "<s>": "~", "</s>": "~", "<strike>": "~", "</strike>": "~",
    }
    texto_whats = texto_html
    for tag, replacement in mapa_tags.items():
        texto_whats = texto_whats.replace(tag, replacement)
    texto_whats = re.sub(r'<a href=[\'"](.*?)[\'"]>(.*?)</a>', r"\1", texto_whats)

    if "http" in texto_whats:
        if not re.search(r"http[s]?://[^\s]+$", texto_whats.strip()):
            partes = texto_whats.split("http")
            if len(partes) > 1:
                link = "http" + partes[-1]
                corpo = "http".join(partes[:-1]).strip()
                texto_whats = f"{corpo}\n\n{link}"
    return texto_whats.strip()


def simular_digitacao(driver, elemento, texto):
    if not SIMULAR_DIGITACAO:
        elemento.send_keys(texto)
        return
    human_delay(0.5, 1.2)
    pyperclip.copy(texto)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    human_delay(1, 2)


def baixar_imagem_temporaria(url_imagem, nome_arquivo="temp_oferta.jpg"):
    try:
        resposta = requests.get(url_imagem, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resposta.status_code == 200:
            with open(nome_arquivo, "wb") as f:
                f.write(resposta.content)
            return os.path.abspath(nome_arquivo)
    except Exception:
        pass
    return None


def copiar_imagem_para_clipboard(caminho_imagem):
    image = Image.open(caminho_imagem)
    output = BytesIO()
    image.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    win32clipboard.CloseClipboard()


def extrair_valor_numerico(preco_texto):
    if not preco_texto:
        return None
    try:
        txt = preco_texto.replace(".", "")
        matches = re.findall(r"\d+,\d+", txt)
        if matches:
            return min([float(m.replace(",", ".")) for m in matches])
        match_simples = re.findall(r"\d+\d*", preco_texto.replace(",", "."))
        return float(match_simples[0]) if match_simples else None
    except Exception:
        return None


def formatar_preco_br(preco):
    try:
        return f"R$ {float(preco):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(preco)


def carregar_cache():
    if not os.path.exists(ARQUIVO_CACHE_ENVIOS):
        return {}
    try:
        with open(ARQUIVO_CACHE_ENVIOS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def salvar_cache(cache):
    with open(ARQUIVO_CACHE_ENVIOS, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4, ensure_ascii=False)


def normalizar_texto(texto):
    return texto.strip().lower() if texto else ""


def verificar_se_ja_enviou_24h(titulo, grupo=None):
    titulo_chave = normalizar_texto(titulo)
    if grupo:
        titulo_chave = f"{grupo}::{titulo_chave}"
    cache = carregar_cache()
    if titulo_chave not in cache:
        return False
    timestamp_envio = cache[titulo_chave]
    agora = time.time()
    if (agora - timestamp_envio) < CACHE_RETENCAO_SECONDS:
        return True
    del cache[titulo_chave]
    salvar_cache(cache)
    return False


def registrar_envio_24h(titulo, grupo=None, url=None):
    cache = carregar_cache()
    titulo_chave = normalizar_texto(titulo)
    if grupo:
        titulo_chave = f"{grupo}::{titulo_chave}"
    cache[titulo_chave] = time.time()

    if url:
        try:
            parsed = urllib.parse.urlparse(url)
            cleaned = parsed._replace(query="").geturl()
            chave_url = normalizar_texto(cleaned)
            if grupo:
                chave_url = f"{grupo}::{chave_url}"
            cache[chave_url] = time.time()
        except Exception:
            pass
    salvar_cache(cache)


def atualizar_historico(arquivo_csv, titulo, preco_coletado):
    if not preco_coletado or preco_coletado <= 0:
        return
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    nova_linha = pd.DataFrame([{"Data": data_hoje, "Preco": preco_coletado, "Produto": titulo}])
    if not os.path.exists(arquivo_csv):
        nova_linha.to_csv(arquivo_csv, index=False)
    else:
        df = pd.read_csv(arquivo_csv)
        if not ((df["Produto"] == titulo) & (df["Data"] == data_hoje)).any():
            pd.concat([df, nova_linha]).to_csv(arquivo_csv, index=False)


def iniciar_driver():
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    print("| 🌐 Conectando ao Chrome na Porta 9222...")
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        print("| ✅ Conexão bem-sucedida com perfil logado!")
        return driver
    except Exception as e:
        print(f"| ⚠️ Chrome 9222 não detectado ({e}). Abrindo nova sessão...")
        options_fallback = Options()
        options_fallback.add_argument("--start-maximized")
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options_fallback)


def focar_aba_whatsapp(driver):
    """Localiza a aba do WhatsApp Web sem recarregar ou abrir abas duplicadas."""
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            time.sleep(0.2)
            url_atual = driver.current_url.lower()
            if "web.whatsapp.com" in url_atual:
                driver.execute_script("window.focus();")
                return handle
        except Exception:
            continue

    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            time.sleep(0.2)
            if "whatsapp" in driver.title.lower():
                driver.execute_script("window.focus();")
                return handle
        except Exception:
            continue

    print("| ⚠️ WhatsApp não detectado. Abrindo nova aba...")
    driver.execute_script("window.open('https://web.whatsapp.com', '_blank');")
    time.sleep(1)
    aba_criada = driver.window_handles[-1]
    driver.switch_to.window(aba_criada)

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//div[@id="side"] | //div[@contenteditable="true"]'))
        )
        print("| ✅ WhatsApp Web carregado com sucesso!")
    except Exception:
        print("| ⏳ Timeout aguardando carregamento do WhatsApp.")

    return aba_criada


def limpar_interface_whatsapp(driver):
    try:
        action = ActionChains(driver)
        action.send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)
        for btn in driver.find_elements(By.XPATH, '//span[@data-icon="x-viewer"] | //div[@aria-label="Fechar"]'):
            try:
                driver.execute_script("arguments[0].click();", btn)
            except Exception:
                pass
    except Exception:
        pass


def enviar_whatsapp_robusto(driver, nome_grupo, mensagem, caminho_imagem=None):
    if not focar_aba_whatsapp(driver):
        return False

    limpar_interface_whatsapp(driver)
    try:
        xpath_pesquisa = (
            '//input[@role="textbox"][@aria-label="Pesquisar ou começar uma nova conversa"] | '
            '//div[@contenteditable="true"][@data-tab="3"] | '
            '//input[@data-tab="3"]'
        )
        caixa = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xpath_pesquisa)))
        caixa.click()
        time.sleep(0.5)
        caixa.send_keys(Keys.CONTROL + "a")
        caixa.send_keys(Keys.BACKSPACE)
        caixa.send_keys(nome_grupo)
        time.sleep(2.5)

        xpath_contato = f'//span[contains(@title, "{nome_grupo}")] | //div[@role="listitem"]//span[contains(text(), "{nome_grupo}")]'
        try:
            alvo = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, xpath_contato)))
            driver.execute_script("arguments[0].click();", alvo)
        except Exception:
            caixa.send_keys(Keys.ENTER)

        time.sleep(3)

        if caminho_imagem and os.path.exists(caminho_imagem):
            copiar_imagem_para_clipboard(caminho_imagem)
            time.sleep(0.5)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(3)

            xpath_legenda = (
                '//div[contains(@class, "lexical-rich-text-input")]//div[@contenteditable="true"] | '
                '//div[@role="textbox"][@contenteditable="true"] | '
                '//div[@aria-label="Adicionar legenda"]'
            )
            legenda_box = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.XPATH, xpath_legenda)))
            driver.execute_script("arguments[0].focus();", legenda_box)
            time.sleep(0.5)

            msg_formatada = formatar_para_whatsapp(mensagem)
            simular_digitacao(driver, legenda_box, msg_formatada)
            time.sleep(1.5)

            try:
                legenda_box.send_keys(Keys.ENTER)
            except Exception:
                pass

            for seletor in ['//div[@aria-label="Enviar"]', '//span[@data-icon="send"]']:
                if botoes := driver.find_elements(By.XPATH, seletor):
                    driver.execute_script("arguments[0].click();", botoes[0])
                    break
        else:
            xpath_caixa_texto = '//div[@id="main"]//div[@contenteditable="true"] | //footer//div[@contenteditable="true"]'
            msg_box = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, xpath_caixa_texto)))
            msg_box.click()
            time.sleep(0.5)
            msg_box.send_keys(Keys.CONTROL + "a")
            msg_box.send_keys(Keys.BACKSPACE)

            msg_formatada = formatar_para_whatsapp(mensagem)
            simular_digitacao(driver, msg_box, msg_formatada)
            time.sleep(3)
            msg_box.send_keys(Keys.ENTER)

        time.sleep(2)
        print(f"| 🚀 Postado com sucesso no WhatsApp: '{nome_grupo}'!")
        return True
    except Exception as e:
        print(f"| ❌ Falha no envio WhatsApp: {e}")
        return False
    finally:
        limpar_interface_whatsapp(driver)


def gerar_chamada_inteligente(titulo, preco_atual, categoria=""):
    t_lower = titulo.lower()

    if any(k in t_lower for k in ["gta", "grand theft auto"]):
        return random.choice([
            "🌴🔥 <b>[HYPE GTA VI / PREPARATIVO DE SETUP]</b>",
            "🚨 <b>[PREPARA O PC PRO GTA VI]</b> Queda de preço confirmada!",
            "🎮 <b>[OPORTUNIDADE GTA VI]</b> Item essencial pro setup atualizado!"
        ])

    if any(k in t_lower for k in ["ps5", "playstation 5", "dualsense", "xbox series", "nintendo switch"]):
        return random.choice([
            "🎮 <b>[CONSOLE & ACESSÓRIOS GAMER]</b>",
            "⚡ <b>[DESCONTO EM CONSOLES]</b> Menor valor identificado no radar!",
            "🕹️ <b>[ALERTA PLAYSTATION / XBOX]</b> Preço no chão para fechar a gameplay!"
        ])

    if any(k in t_lower for k in ["rtx", "gtx", "rx", "placa de vídeo", "ssd", "nvme", "ryzen", "core i5", "core i7", "memória ram", "b650", "b550", "fonte"]):
        return random.choice([
            "🖥️ <b>[UPGRADE PC GAMER]</b> Peça de alta performance em oferta!",
            "🚀 <b>[HARDWARE NA PROMO]</b> Ótima relação custo-benefício por FPS!",
            "💥 <b>[ALERTA HARDWARE]</b> Desconto ativo no componente!"
        ])

    if any(k in t_lower for k in ["mouse", "teclado", "headset", "mousepad", "headphone"]):
        return random.choice([
            "⌨️ <b>[PERIFÉRICO TECH & GAMES]</b> Custo-benefício de peso na tela!",
            "🎧 <b>[SETUP GAMER COMPLETO]</b> Equipamento aprovado para competitivo!",
            "🖱️ <b>[UPGRADE DE PERIFÉRICO]</b> Menor preço registrado no radar!"
        ])

    if any(k in t_lower for k in ["monitor", "144hz", "165hz", "180hz", "240hz", "oled", "ips"]):
        return random.choice([
            "📺 <b>[MONITOR GAMER COMPETITIVO]</b> Alta taxa de atualização no precinho!",
            "⚡ <b>[ALERTA DE TELA]</b> Ideal para jogar liso sem ghosting!"
        ])

    return random.choice([
        "🔥 <b>[SUPER OFERTA TECH & GAMES]</b>",
        "⚡ <b>[OPORTUNIDADE GAMER]</b> Menor valor identificado!",
        "🎯 <b>[ACHADO TECH NO PRECINHO]</b> Desconto verificado no radar!"
    ])


def gerar_link_ml_via_barra_topo(driver):
    try:
        def validar_link_ml(link_val):
            return bool(link_val and any(d in link_val for d in ["meli.la", "mercadolivre", "ml.com"]))

        seletores_botao = [
            "button[data-testid='generate_link_button']",
            "//button[contains(., 'Compartilhar')]",
            "//button[contains(., 'compartilhar')]",
            "[data-testid='share-button']",
            "//button[contains(., 'Gerar link')]",
        ]

        for seletor in seletores_botao:
            try:
                tipo = By.XPATH if "//" in seletor else By.CSS_SELECTOR
                btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((tipo, seletor)))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", btn)
                break
            except Exception:
                continue

        time.sleep(2)
        seletores_input = [
            "input.andes-form-control__field",
            ".andes-form-control__field input",
            "input[data-testid='copy_link_input']",
            "input[type='text'][value*='meli']",
            "input[type='text'][value*='mercadolivre']",
        ]
        for seletor in seletores_input:
            try:
                caixa = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, seletor)))
                v = caixa.get_attribute("value")
                if validar_link_ml(v):
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    return v
            except Exception:
                continue

        atual = driver.current_url
        if atual and any(dom in atual for dom in ["meli.la", "mercadolivre", "ml.com"]):
            parsed = urllib.parse.urlparse(atual)
            cleaned = parsed._replace(query="").geturl()
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            return cleaned
    except Exception:
        pass
    finally:
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
    return None


def gerar_link_amazon_sitestripe(driver):
    try:
        seletores_botao = [
            "a[title='Texto']", "#amzn-ss-text-link a", 
            "li#amzn-ss-text-link a", "#amzn-ss-get-link-button"
        ]
        botao_abrir = None
        for seletor in seletores_botao:
            try:
                botao_abrir = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.CSS_SELECTOR, seletor)))
                break
            except Exception:
                continue

        if not botao_abrir:
            return None

        driver.execute_script("arguments[0].click();", botao_abrir)
        time.sleep(2.5)

        seletores_caixa = [
            "#amzn-ss-text-shortlink-textarea", "textarea[readonly]", ".amzn-ss-text-shortlink-textarea"
        ]
        link_curto = None
        for seletor in seletores_caixa:
            try:
                campo = WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.CSS_SELECTOR, seletor)))
                link_curto = campo.get_attribute("value") or campo.text
                if link_curto and "amzn" in link_curto:
                    break
            except Exception:
                continue

        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        return link_curto
    except Exception:
        return None


def gerar_link_aliexpress_barra(driver):
    try:
        seletores_botao = [
            "//div[contains(@class, 'portals-bar')]//span[contains(text(), 'Text')]",
            "//span[contains(@class, 'ae-affiliate-link')]",
            "//div[contains(@id, 'header-affiliate')]//a",
            "div.affiliate-link-generator",
        ]
        for seletor in seletores_botao:
            try:
                tipo = By.XPATH if "//" in seletor else By.CSS_SELECTOR
                btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((tipo, seletor)))
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(2)
                break
            except Exception:
                continue

        seletores_link = [
            "input.affiliate-short-url", "textarea.link-url", 
            "input[value*='s.click.aliexpress.com']", ".ae-affiliate-url-box input"
        ]
        link_final = None
        for seletor in seletores_link:
            try:
                tipo = By.XPATH if "//" in seletor else By.CSS_SELECTOR
                campo = WebDriverWait(driver, 4).until(EC.presence_of_element_located((tipo, seletor)))
                v = campo.get_attribute("value") or campo.text
                if v and "s.click.aliexpress.com" in v:
                    link_final = v
                    break
            except Exception:
                continue

        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        return link_final
    except Exception:
        return None


def extrair_dados_produto_ml(driver):
    titulo, image_url = "Produto Mercado Livre", None
    preco_atual, preco_antigo = None, None

    try:
        titulo = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1.ui-pdp-title"))
        ).text.strip()
    except Exception:
        pass

    try:
        container_preco = driver.find_element(By.CSS_SELECTOR, ".ui-pdp-price__second-line")
        fraction = container_preco.find_element(By.CSS_SELECTOR, ".andes-money-amount__fraction").text.replace(".", "")
        try:
            cents = container_preco.find_element(By.CSS_SELECTOR, ".andes-money-amount__cents").text
            preco_atual = float(f"{fraction}.{cents}")
        except Exception:
            preco_atual = float(fraction)

        try:
            old_fraction = driver.find_element(
                By.CSS_SELECTOR, ".ui-pdp-price__original-value .andes-money-amount__fraction"
            ).text.replace(".", "")
            preco_antigo = float(old_fraction)
        except Exception:
            pass
    except Exception:
        pass

    try:
        img = driver.find_element(By.CSS_SELECTOR, "figure.ui-pdp-gallery__figure img, img.ui-pdp-image")
        image_url = img.get_attribute("src")
        if image_url and "mlstatic.com" in image_url:
            image_url = re.sub(r"-[A-Z]\.(webp|jpg|jpeg|png)$", r"-O.\1", image_url).replace("D_Q_NP_", "D_NQ_NP_")
    except Exception:
        pass

    return titulo, preco_atual, preco_antigo, image_url


def processar_feed_mercadolivre(driver, alvo, produtos_processados_set):
    print(f"\n======== 🎮 {alvo['nome']} ========")
    driver.get(alvo["url_lista"])
    time.sleep(3)

    links_para_visitar = []
    seletores_card = [".poly-card", ".ui-search-result", ".andes-card", "[data-item-id]"]
    seletores_link = ["a.poly-component__title", "a.ui-search-result__link", "a[href*='mercadolivre']"]

    try:
        cards = []
        for seletor in seletores_card:
            cards = driver.find_elements(By.CSS_SELECTOR, seletor)
            if cards:
                break

        for card in cards[:30]:
            for s_link in seletores_link:
                try:
                    url = card.find_element(By.CSS_SELECTOR, s_link).get_attribute("href")
                    if url and "mercadolivre" in url:
                        links_para_visitar.append(url)
                        break
                except Exception:
                    continue
    except Exception as e:
        print(f"| ❌ Erro ao ler lista ML: {e}")
        return

    produtos_enviados = 0
    grupo_principal = GRUPOS_ALVO[0]

    for url_produto in links_para_visitar:
        if produtos_enviados >= 5:
            break

        if verificar_se_ja_enviou_24h(url_produto, grupo_principal):
            continue

        try:
            driver.get(url_produto)
            titulo, preco_atual, preco_antigo, img_url = extrair_dados_produto_ml(driver)

            if titulo in produtos_processados_set or not preco_atual:
                continue

            if preco_atual < PRECO_MINIMO_TECH:
                print(f"| 💸 Descartado (Piso Tech): R$ {preco_atual:.2f} < R$ {PRECO_MINIMO_TECH:.0f}")
                continue

            if not produto_eh_marca_confiavel(titulo):
                print(f"| 🛑 Descartado (Marca genérica/fora do nicho): {titulo[:40]}...")
                continue

            if produto_eh_bloqueado(titulo):
                continue

            link_afiliado = gerar_link_ml_via_barra_topo(driver)
            if not link_afiliado:
                continue

            chamada = gerar_chamada_inteligente(titulo, preco_atual, alvo.get("categoria", ""))
            bloco_preco = f"✅ <b>Por: {formatar_preco_br(preco_atual)}</b>"
            if preco_antigo and preco_antigo > preco_atual:
                desconto = int(((preco_antigo - preco_atual) / preco_antigo) * 100)
                bloco_preco = (
                    f"❌ <s>De: {formatar_preco_br(preco_antigo)}</s>\n"
                    f"✅ <b>Por: {formatar_preco_br(preco_atual)}</b> ({desconto}% OFF) 📉"
                )

            mensagem = (
                f"{chamada}\n\n"
                f"🔥 <b>{titulo.strip()}</b>\n\n"
                f"{bloco_preco}\n\n"
                f"🛒 <b>Acesse a oferta:</b>\n"
                f"👉 {link_afiliado}\n\n"
                f"<i>⚠️ Preço sujeito a alteração rápida!</i>"
            )

            enviar_telegram(mensagem, link_afiliado, img_url)
            caminho_foto = baixar_imagem_temporaria(img_url, "temp_ml.jpg") if img_url else None

            sucesso = enviar_whatsapp_robusto(driver, grupo_principal, mensagem, caminho_foto)
            if caminho_foto and os.path.exists(caminho_foto):
                try:
                    os.remove(caminho_foto)
                except Exception:
                    pass

            if sucesso:
                registrar_envio_24h(titulo, grupo_principal, url=url_produto)
                produtos_processados_set.add(titulo)
                produtos_enviados += 1
                print(f"| 🎯 Oferta enviada: {produtos_enviados}/5 ({alvo['nome']})")
                
                # Devolve o foco para o Mercado Livre com garantia
                for handle in driver.window_handles:
                    try:
                        driver.switch_to.window(handle)
                        if "mercadolivre.com" in driver.current_url:
                            break
                    except Exception:
                        continue

                human_delay(DELAY_MIN_ENTRE_MENSAGENS, DELAY_MAX_ENTRE_MENSAGENS)

        except Exception as e:
            print(f"| ❌ Falha no produto: {e}")
            continue


def rastrear_lista_produtos(url_lista, driver, seletor_item, dominio_base, max_list_items=35):
    print(f"[SELENIUM] Acessando: {url_lista}")
    driver.get(url_lista)
    time.sleep(2)

    for _ in range(4):
        driver.execute_script("window.scrollBy(0, 600);")
        time.sleep(1)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    itens = soup.select(seletor_item)
    encontrados = []

    for item in itens[:max_list_items]:
        try:
            link_tag = item if item.name == "a" and item.has_attr("href") else item.select_one("a")
            if link_tag and link_tag.get("href"):
                url_c = link_tag.get("href")
                if not url_c.startswith("http"):
                    url_c = dominio_base + url_c
                if url_c.startswith("//"):
                    url_c = "https:" + url_c

                titulo = ""
                if tit_tag := item.select_one('#productTitle, span.line-clamp-2, h1, h2, h3, [data-pl="product-title"]'):
                    titulo = tit_tag.get_text(strip=True)
                elif img_tag := item.select_one("img"):
                    titulo = img_tag.get("title") or img_tag.get("alt") or ""

                if titulo and len(titulo) > 5:
                    encontrados.append({"titulo": titulo, "url": url_c})
        except Exception:
            continue
    return encontrados


def rastrear_detalhe_produto(produto, driver):
    url_produto = produto["url"]
    try:
        driver.get(url_produto)
    except Exception:
        return produto["titulo"], None, None, None

    time.sleep(1.5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    titulo_final = produto["titulo"]
    preco_atual, preco_antigo, image_url = None, None, None

    if "amazon" in url_produto:
        try:
            if elem_t := soup.select_one("#productTitle"):
                titulo_final = elem_t.get_text(strip=True)
            for sel in [".a-price.aok-align-center .a-offscreen", "#price", "#newBuyBoxPrice", ".a-price .a-offscreen"]:
                if elem_p := soup.select_one(sel):
                    if (v := extrair_valor_numerico(elem_p.get_text())) and v > 0:
                        preco_atual = v
                        break
            if elem_antigo := soup.select_one('span[data-a-strike="true"] .a-offscreen, #listPrice'):
                preco_antigo = extrair_valor_numerico(elem_antigo.get_text())
            if img_elem := soup.select_one('img#landingImage, img#imgBlkFront, [data-a-image-name="landingImage"]'):
                image_url = img_elem.get("data-zoom") or img_elem.get("src")
                if image_url:
                    image_url = re.sub(r"\._AC_.*_\.", ".", image_url)
        except Exception:
            pass

    elif "aliexpress" in url_produto:
        try:
            if elem_t := soup.select_one('h1[data-pl="product-title"], .product-title'):
                titulo_final = elem_t.get_text(strip=True)
            if elem_p := soup.select_one(".product-price-value, .price--currentPriceText--2gXyvgK, span[class*='currentPrice']"):
                preco_atual = extrair_valor_numerico(elem_p.get_text())
            if elem_old := soup.select_one(".product-price-original, span[class*='originalPrice']"):
                preco_antigo = extrair_valor_numerico(elem_old.get_text())
            if img_elem := soup.select_one("img.magnifier-image, .pdp-info-right img, img[class*='mainImage']"):
                image_url = img_elem.get("src")
                if image_url and image_url.startswith("//"):
                    image_url = "https:" + image_url
        except Exception:
            pass

    return titulo_final, preco_atual, preco_antigo, image_url


# --- LISTAS MESTRAS DE PRODUTOS TECH, GAMES E HARDWARE ---
LISTA_MESTRE_ML = [
    {
        "nome": "ML - Loja Oficial Logitech G",
        "url_lista": "https://loja.mercadolivre.com.br/logitech-g",
        "loja": "MERCADOLIVRE",
        "categoria": "PERIFERICOS",
    },
    {
        "nome": "ML - Loja Oficial Redragon",
        "url_lista": "https://loja.mercadolivre.com.br/redragon",
        "loja": "MERCADOLIVRE",
        "categoria": "PERIFERICOS",
    },
    {
        "nome": "ML - Placas de Vídeo & Hardware",
        "url_lista": "https://lista.mercadolivre.com.br/informatica/componentes-pc/placas-video/_PriceRange_300-0_NoIndex_True",
        "loja": "MERCADOLIVRE",
        "categoria": "PCGAMER",
    },
    {
        "nome": "ML - Consoles PS5 e Xbox Series",
        "url_lista": "https://lista.mercadolivre.com.br/games/consoles-video-game/_PriceRange_1500-0_NoIndex_True",
        "loja": "MERCADOLIVRE",
        "categoria": "GAMES",
    }
]

LISTA_MESTRE_AMAZON = [
    {
        "nome": "Amazon - Peças & Componentes PC",
        "url_lista": "https://www.amazon.com.br/gp/bestsellers/computers/16364756011/",
        "dominio_base": "https://www.amazon.com.br",
        "loja": "AMAZON",
        "seletor_item_lista": 'div[id^="p13n-asin-index-"], div.zg-grid-general-faceout',
        "categoria": "PCGAMER",
    },
    {
        "nome": "Amazon - Periféricos Gamer",
        "url_lista": "https://www.amazon.com.br/gp/bestsellers/computers/16364811011/",
        "dominio_base": "https://www.amazon.com.br",
        "loja": "AMAZON",
        "seletor_item_lista": 'div[id^="p13n-asin-index-"], div.zg-grid-general-faceout',
        "categoria": "PERIFERICOS",
    },
    {
        "nome": "Amazon - Consoles e Jogos",
        "url_lista": "https://www.amazon.com.br/gp/bestsellers/videogames/",
        "dominio_base": "https://www.amazon.com.br",
        "loja": "AMAZON",
        "seletor_item_lista": 'div[id^="p13n-asin-index-"], div.zg-grid-general-faceout',
        "categoria": "GAMES",
    },
]

LISTA_MESTRE_ALIEXPRESS = [
    {
        "nome": "AliExpress - Periféricos e Teclados Mecânicos",
        "url_lista": "https://pt.aliexpress.com/w/wholesale-gaming-mouse-keyboard.html",
        "dominio_base": "https://pt.aliexpress.com",
        "loja": "ALIEXPRESS",
        "seletor_item_lista": "div.search-card-item, a.search-card-item",
        "categoria": "PERIFERICOS",
    }
]


# --- LOOP PRINCIPAL ---
def main(alvos_a_rodar):
    print("--- INICIANDO RASTREADOR: MODO TECH & GAMES ---")
    driver = iniciar_driver()
    aba_whatsapp = focar_aba_whatsapp(driver)

    if driver.current_window_handle == aba_whatsapp:
        driver.execute_script("window.open('about:blank', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
    aba_navegacao = driver.current_window_handle

    produtos_processados = set()
    grupo_principal = GRUPOS_ALVO[0]

    try:
        for alvo in alvos_a_rodar:
            try:
                driver.switch_to.window(aba_navegacao)
            except Exception:
                pass

            if alvo.get("loja") == "MERCADOLIVRE":
                processar_feed_mercadolivre(driver, alvo, produtos_processados)
                continue

            print(f"\n======== 🎮 {alvo['nome']} ========")
            try:
                ofertas_dinamicas = rastrear_lista_produtos(
                    alvo["url_lista"], driver, alvo.get("seletor_item_lista"), alvo.get("dominio_base", "")
                )
            except Exception as e:
                print(f"| ❌ Falha ao carregar lista: {e}")
                continue

            produtos_validos = 0
            for produto in ofertas_dinamicas:
                if produtos_validos >= 5:
                    break

                if verificar_se_ja_enviou_24h(produto["titulo"], grupo_principal):
                    continue

                driver.switch_to.window(aba_navegacao)
                titulo, preco_atual, preco_antigo, image_url = rastrear_detalhe_produto(produto, driver)

                if not preco_atual or titulo in produtos_processados:
                    continue

                if preco_atual < PRECO_MINIMO_TECH:
                    print(f"| 💸 Descartado (Piso Tech): R$ {preco_atual:.2f} < R$ {PRECO_MINIMO_TECH:.0f}")
                    continue

                if not produto_eh_marca_confiavel(titulo):
                    print(f"| 🛑 Descartado (Marca genérica/fora do nicho): {titulo[:40]}...")
                    continue

                if produto_eh_bloqueado(titulo):
                    continue

                url_final = None
                if alvo.get("loja") == "AMAZON":
                    url_final = gerar_link_amazon_sitestripe(driver)
                elif alvo.get("loja") == "ALIEXPRESS":
                    url_final = gerar_link_aliexpress_barra(driver)

                if not url_final:
                    continue

                chamada = gerar_chamada_inteligente(titulo, preco_atual, alvo.get("categoria", ""))
                bloco_preco = f"✅ <b>Por: {formatar_preco_br(preco_atual)}</b>"
                if preco_antigo and preco_antigo > preco_atual:
                    desconto = int(((preco_antigo - preco_atual) / preco_antigo) * 100)
                    bloco_preco = (
                        f"❌ <s>De: {formatar_preco_br(preco_antigo)}</s>\n"
                        f"✅ <b>Por: {formatar_preco_br(preco_atual)}</b> ({desconto}% OFF) 📉"
                    )

                mensagem_final = (
                    f"{chamada}\n\n"
                    f"🔥 <b>{titulo.strip()}</b>\n\n"
                    f"{bloco_preco}\n\n"
                    f"🛒 <b>Acesse a oferta:</b>\n"
                    f"👉 {url_final}\n\n"
                    f"<i>⚠️ Preço sujeito a alteração rápida!</i>"
                )

                enviar_telegram(mensagem_final, url_final, image_url)
                caminho_foto = baixar_imagem_temporaria(image_url) if image_url else None

                sucesso = enviar_whatsapp_robusto(driver, grupo_principal, mensagem_final, caminho_foto)
                if caminho_foto and os.path.exists(caminho_foto):
                    try:
                        os.remove(caminho_foto)
                    except Exception:
                        pass

                if sucesso:
                    registrar_envio_24h(titulo, grupo_principal, url=url_final)
                    produtos_processados.add(titulo)
                    produtos_validos += 1
                    print(f"| 🎯 Oferta enviada: {produtos_validos}/5 ({alvo['nome']})")
                    
                    try:
                        driver.switch_to.window(aba_navegacao)
                    except Exception:
                        pass

                    human_delay(DELAY_MIN_ENTRE_MENSAGENS, DELAY_MAX_ENTRE_MENSAGENS)

    except Exception as e:
        print(f"| ❌ Erro de execução: {e}")
    finally:
        print("--- CICLO CONCLUÍDO ---")


if __name__ == "__main__":
    LISTA_TRIO = LISTA_MESTRE_ML + LISTA_MESTRE_AMAZON + LISTA_MESTRE_ALIEXPRESS
    main(LISTA_TRIO)

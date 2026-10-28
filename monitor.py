#!/usr/bin/env python3
"""
Monitor do streaming da Rádio Terra Nativa FM.

A cada execução (o GitHub roda de 5 em 5 minutos) faz 2 conferências, com uns 2 minutos e meio entre elas:
  1. conecta no streaming como um ouvinte e ouve ~20 segundos (nada é salvo);
  2. confere se conectou, se o áudio chegou inteiro, se chegou no ritmo certo (sem picotar)
     e se tem som (não só silêncio);
  3. se der problema, tenta de novo 15 segundos depois; confirmado, avisa no Telegram na hora;
  4. lembra de hora em hora enquanto continuar fora e avisa quando voltar;
  5. guarda a situação em estado.json.

Configuração (no GitHub, em Settings > Secrets and variables > Actions):
  TELEGRAM_TOKEN    token do robô (do @BotFather)
  TELEGRAM_CHAT_ID  quem recebe; para mais de um, separe por vírgula
Opcionais (em Variables): STREAM_URL, NOME_RADIO
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

STREAM_URL = os.environ.get('STREAM_URL') or 'https://ice.fabricahost.com.br/culturaandira'
NOME = os.environ.get('NOME_RADIO') or 'Rádio Terra Nativa FM'
TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
CHATS = [c.strip() for c in os.environ.get('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
TELEGRAM_API = os.environ.get('TELEGRAM_API', 'https://api.telegram.org')
ARQ_ESTADO = os.environ.get('ARQ_ESTADO', 'estado.json')

SEGUNDOS = 20            # quanto do streaming é gravado em cada conferência
FALHAS_PARA_AVISAR = 1   # cada conferência já confirma o problema com uma 2ª tentativa antes de contar
INTERVALO_SEG = 150      # tempo entre as conferências da mesma execução
CONFERENCIAS = 2         # conferências por execução
LENTIDAO = 1.30          # levar 30% a mais que o tempo real para receber o áudio = picotando
LEMBRETE_MIN = 60        # enquanto estiver fora, lembra a cada X minutos
SILENCIO_DB = -50.0      # pico abaixo disso = silêncio
BRT = timezone(timedelta(hours=-3))


def agora():
    return datetime.now(BRT)


def fmt(dt):
    return dt.strftime('%d/%m %H:%M')


def duracao(seg):
    seg = int(seg)
    h, m = seg // 3600, (seg % 3600) // 60
    return f'{h}h{m:02d}min' if h else f'{max(m, 1)} min'


def conferir():
    """Grava um trecho do streaming e devolve (ok, motivo, detalhes)."""
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'info',
           '-rw_timeout', '15000000', '-reconnect', '0',
           '-i', STREAM_URL, '-t', str(SEGUNDOS), '-vn',
           '-af', 'volumedetect', '-f', 'null', '-']
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=SEGUNDOS + 40)
        log = p.stderr
        gasto = time.time() - t0
    except subprocess.TimeoutExpired:
        return False, 'o streaming não respondeu (tempo esgotado)', {}
    except FileNotFoundError:
        print('ffmpeg não encontrado', file=sys.stderr)
        sys.exit(2)
    det = {}
    m = re.findall(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', log)
    if m:
        h, mi, s = m[-1]
        det['segundos'] = round(int(h) * 3600 + int(mi) * 60 + float(s), 1)
    mv = re.search(r'max_volume:\s*(-?[\d.]+|-inf) dB', log)
    av = re.search(r'mean_volume:\s*(-?[\d.]+|-inf) dB', log)
    if mv:
        det['pico_db'] = float('-inf') if mv.group(1) == '-inf' else float(mv.group(1))
    if av:
        det['media_db'] = float('-inf') if av.group(1) == '-inf' else float(av.group(1))
    if p.returncode != 0 and not det.get('segundos'):
        erro = ''
        for linha in log.splitlines():
            if re.search(r'(Server returned|Connection|Failed|Error|error|refused|404|403|500|502|503|resolve)', linha):
                erro = linha.strip()
        return False, 'não consegui conectar no streaming' + (f' ({erro[-120:]})' if erro else ''), det
    if det.get('segundos', 0) < SEGUNDOS * 0.6:
        return False, f"o streaming conectou mas o áudio parou (chegaram só {det.get('segundos', 0)}s de {SEGUNDOS}s)", det
    # picotando: o áudio chega mais devagar que o tempo real (o ouvinte fica parando para carregar)
    det['espera_s'] = round(gasto, 1)
    if det.get('segundos') and gasto > det['segundos'] * LENTIDAO + 3:
        return False, f"o streaming está PICOTANDO (levou {round(gasto)}s para chegar {det['segundos']}s de áudio) — internet da rádio fraca?", det
    if 'pico_db' in det and det['pico_db'] < SILENCIO_DB:
        return False, f"o streaming está no ar, mas só com SILÊNCIO (pico {det['pico_db']} dB) — Playlist parado ou mesa sem sinal?", det
    return True, 'no ar', det


def frase(m):
    m = m[0].upper() + m[1:]
    return m if m[-1] in '.?!' else m + '.'


def telegram(texto):
    if not TOKEN or not CHATS:
        print('[sem Telegram configurado] ' + texto)
        return True
    tudo_ok = True
    for chat in CHATS:
        dados = urllib.parse.urlencode({'chat_id': chat, 'text': texto, 'disable_web_page_preview': 'true'}).encode()
        try:
            with urllib.request.urlopen(f'{TELEGRAM_API}/bot{TOKEN}/sendMessage', data=dados, timeout=20) as r:
                r.read()
        except Exception as e:
            tudo_ok = False
            print(f'Falha ao avisar no Telegram ({chat}): {e}', file=sys.stderr)
    return tudo_ok


def ler_estado():
    try:
        with open(ARQ_ESTADO, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'situacao': 'ok', 'falhas': 0, 'desde': None, 'ultimo_aviso': None, 'avisado': False, 'historico': []}


def gravar_estado(e):
    with open(ARQ_ESTADO, 'w', encoding='utf-8') as f:
        json.dump(e, f, ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'teste':
        ok, motivo, det = conferir()
        situ = '✅ no ar' if ok else '⚠️ ' + motivo
        enviado = telegram(f'🧪 Teste do monitor — {NOME}\nSituação agora: {situ}\n'
                           f"Detalhes: {det.get('segundos', '?')}s recebidos, pico {det.get('pico_db', '?')} dB\n"
                           f'Se você recebeu esta mensagem, os avisos estão funcionando.')
        print(situ, det)
        sys.exit(0 if enviado else 1)

    e = ler_estado()
    for n in range(CONFERENCIAS):
        if n:
            time.sleep(INTERVALO_SEG)
        uma_conferencia(e)
        gravar_estado(e)


def uma_conferencia(e):
    ok, motivo, det = conferir()
    # uma segunda tentativa rápida antes de contar como falha (soluço de rede)
    if not ok:
        time.sleep(15)
        ok2, motivo2, det2 = conferir()
        if ok2:
            ok, motivo, det = ok2, motivo2, det2
        else:
            motivo, det = motivo2, det2
    t = agora()
    print(t.isoformat(), 'OK' if ok else 'FALHA', motivo, det, flush=True)

    if ok:
        if e.get('situacao') == 'fora' and e.get('avisado'):
            ini = datetime.fromisoformat(e['desde'])
            telegram(f"✅ {NOME} VOLTOU AO AR ({fmt(t)}).\nFicou com problema por cerca de {duracao((t - ini).total_seconds())}, desde {fmt(ini)}.")
            e.setdefault('historico', []).append({'inicio': e['desde'], 'fim': t.isoformat(), 'motivo': e.get('motivo')})
            e['historico'] = e['historico'][-50:]
        e.update({'situacao': 'ok', 'falhas': 0, 'desde': None, 'ultimo_aviso': None, 'avisado': False, 'motivo': None})
    else:
        if e.get('situacao') != 'fora':
            e.update({'situacao': 'fora', 'falhas': 0, 'desde': t.isoformat(), 'avisado': False, 'ultimo_aviso': None})
        e['falhas'] = e.get('falhas', 0) + 1
        e['motivo'] = motivo
        ini = datetime.fromisoformat(e['desde'])
        if not e.get('avisado') and e['falhas'] >= FALHAS_PARA_AVISAR:
            if telegram(f'🔴 ALERTA — {NOME} COM PROBLEMA\n{frase(motivo)}\nDesde {fmt(ini)}.\nLink: {STREAM_URL}'):
                e['avisado'] = True
                e['ultimo_aviso'] = t.isoformat()
        elif e.get('avisado') and e.get('ultimo_aviso'):
            ult = datetime.fromisoformat(e['ultimo_aviso'])
            if (t - ult).total_seconds() >= LEMBRETE_MIN * 60:
                if telegram(f'🔴 {NOME} continua com problema há {duracao((t - ini).total_seconds())}.\n{frase(motivo)}'):
                    e['ultimo_aviso'] = t.isoformat()
    e['ultima_conferencia'] = t.isoformat()
    e['ultimo_resultado'] = {'ok': ok, 'motivo': motivo, **{k: (None if v == float('-inf') else v) for k, v in det.items()}}


if __name__ == '__main__':
    main()

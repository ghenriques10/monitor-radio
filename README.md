# Monitor do streaming — Rádio Terra Nativa FM

De 5 em 5 minutos o GitHub conecta no streaming, grava 20 segundos e confere se a rádio está no ar **e com som**.
Se der problema em 2 conferências seguidas, avisa no Telegram; enquanto continuar fora, lembra de hora em hora;
quando voltar, avisa quanto tempo ficou fora.

- `monitor.py` — a conferência e os avisos
- `.github/workflows/monitor.yml` — o agendamento
- `estado.json` — criado sozinho: a situação atual e o histórico das quedas

#!/usr/bin/env bash
# Установка / обновление Trading Desk бота на VPS (Ubuntu / Debian).
#
#   curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/TradingBot/HEAD/install.sh | sudo bash
#
# Повторный запуск = обновление (данные, .env и ключи сохраняются).
# Без вопросов:  ... | sudo BOT_TOKEN=123:abc ALLOWED_USERS=111 TZ_NAME=Asia/Ashgabat bash
set -euo pipefail

REPO="${REPO:-https://github.com/k9032431-cmd/TradingBot.git}"
BRANCH="${BRANCH:-}"
DIR="${DIR:-/opt/tradingbot}"
SVC_USER="tradingbot"
SERVICE="tradingbot"

G='\033[1;32m'; R='\033[1;31m'; B='\033[1;34m'; Y='\033[1;33m'; N='\033[0m'
step() { echo -e "\n${B}▸${N} $*"; }
ok()   { echo -e "  ${G}✓${N} $*"; }
warn() { echo -e "  ${Y}!${N} $*"; }
die()  { echo -e "\n${R}✗ $*${N}" >&2; exit 1; }

# читаем ответы с терминала, даже когда скрипт пришёл через curl | bash
has_tty() { { true </dev/tty; } 2>/dev/null; }
ask() {  # ask "Вопрос" "по умолчанию" -> ответ
  local answer=""
  if has_tty; then
    read -r -p "  $1${2:+ [$2]}: " answer </dev/tty || return 1
  fi
  echo "${answer:-$2}"
}

# весь скрипт в функции: bash дочитает его целиком до запуска, и apt не «съест» остаток из curl | bash
main() {
[[ $EUID -eq 0 ]] || die "Запусти от root:  curl -fsSL ... | sudo bash"
command -v apt-get >/dev/null || die "Нужен Ubuntu или Debian (apt-get не найден)"

echo -e "${G}"
echo "  ╔══════════════════════════════════╗"
echo "  ║   💎  Trading Desk — установка    ║"
echo "  ╚══════════════════════════════════╝"
echo -e "${N}"

# ---------- системные пакеты ----------
step "Устанавливаю системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip >/dev/null

PY=python3
if ! $PY -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  warn "Системный Python $($PY -V | cut -d' ' -f2) слишком старый, нужен 3.10+"
  if grep -qi ubuntu /etc/os-release; then
    apt-get install -y -qq software-properties-common >/dev/null
    add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
    apt-get update -qq
    apt-get install -y -qq python3.11 python3.11-venv >/dev/null
    PY=python3.11
  else
    die "Обнови систему до Debian 12 / Ubuntu 22.04 или новее"
  fi
fi
ok "$($PY -V)"

# ---------- пользователь и код ----------
step "Скачиваю бота в $DIR"
id "$SVC_USER" &>/dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$SVC_USER"
as_bot() { runuser -u "$SVC_USER" -- "$@"; }

if [[ -d "$DIR/.git" ]]; then
  chown -R "$SVC_USER:$SVC_USER" "$DIR"
  as_bot git -C "$DIR" fetch -q origin
  target="origin/${BRANCH:-$(as_bot git -C "$DIR" rev-parse --abbrev-ref HEAD)}"
  as_bot git -C "$DIR" reset -q --hard "$target"   # .env, data/, emoji.json не в git — не трогаются
  ok "Обновлено до $(as_bot git -C "$DIR" log -1 --format='%h %s')"
  UPDATING=1
else
  mkdir -p "$DIR"
  chown "$SVC_USER:$SVC_USER" "$DIR"
  as_bot git clone -q ${BRANCH:+-b "$BRANCH"} "$REPO" "$DIR"
  ok "Склонировано"
  UPDATING=0
fi

step "Ставлю зависимости Python"
[[ -x "$DIR/.venv/bin/python" ]] || as_bot "$PY" -m venv "$DIR/.venv"
as_bot "$DIR/.venv/bin/pip" install -q --upgrade pip
as_bot "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
ok "Готово"

# ---------- настройки ----------
ENV_FILE="$DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  step "Настройка"
  echo "  Токен бота: @BotFather → /newbot (или /token для существующего)"
  token="${BOT_TOKEN:-}"
  while true; do
    if [[ ! "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
      [[ -n "$token" ]] && warn "Не похоже на токен, попробуй ещё раз"
      has_tty || die "Передай токен: ... | sudo BOT_TOKEN=123:abc bash"
      token="$(ask "BOT_TOKEN" "")" || die "Ввод прерван"
      continue
    fi
    me="$(curl -s -m 10 "https://api.telegram.org/bot$token/getMe" || true)"
    if [[ "$me" == *'"ok":true'* ]]; then
      ok "Бот найден: @$(sed -E 's/.*"username":"([^"]+)".*/\1/' <<<"$me")"
      break
    elif [[ "$me" == *'"error_code":401'* || "$me" == *'"error_code":404'* ]]; then
      warn "Telegram не принял этот токен"
      has_tty || die "Неверный BOT_TOKEN"
      token=""
    else
      warn "Не удалось проверить токен (нет связи с Telegram) — продолжаю"
      break
    fi
  done

  echo "  Свой Telegram ID можно узнать у @userinfobot. Пусто — ботом смогут пользоваться все."
  users="${ALLOWED_USERS-$(ask "ALLOWED_USERS (ID через запятую)" "" || true)}"
  tz="${TZ_NAME:-$(ask "Часовой пояс для статистики" "Asia/Ashgabat" || echo Asia/Ashgabat)}"
  [[ -f "/usr/share/zoneinfo/$tz" ]] || { warn "Неизвестный пояс $tz, ставлю UTC"; tz="UTC"; }

  umask 077
  cat >"$ENV_FILE" <<EOF
BOT_TOKEN=$token
ALLOWED_USERS=$users
ALERT_INTERVAL=10
SYNC_INTERVAL=60
DB_PATH=$DIR/data/trading.db
TZ=$tz
EOF
  umask 022
  chown "$SVC_USER:$SVC_USER" "$ENV_FILE"
  ok ".env создан"
else
  ok "Настройки сохранены с прошлой установки ($ENV_FILE)"
fi
mkdir -p "$DIR/data" && chown -R "$SVC_USER:$SVC_USER" "$DIR/data" && chmod 700 "$DIR/data"

# ---------- автозапуск ----------
step "Настраиваю автозапуск (systemd)"
cat >/etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Trading Desk Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$DIR
EnvironmentFile=$ENV_FILE
ExecStart=$DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat >/usr/local/bin/tbot <<'EOF'
#!/usr/bin/env bash
# Управление ботом: tbot status | logs | restart | stop | start | update | config
case "${1:-status}" in
  status)  systemctl status tradingbot --no-pager ;;
  logs)    journalctl -u tradingbot -f -n 100 ;;
  restart|stop|start) sudo systemctl "$1" tradingbot && echo "ok: $1" ;;
  update)  curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/TradingBot/HEAD/install.sh | sudo bash ;;
  config)  sudo "${EDITOR:-nano}" /opt/tradingbot/.env && sudo systemctl restart tradingbot ;;
  *)       echo "tbot status | logs | restart | stop | start | update | config" ;;
esac
EOF
chmod +x /usr/local/bin/tbot

systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 4
if systemctl is-active -q "$SERVICE"; then
  ok "Бот запущен и будет стартовать сам после перезагрузки сервера"
else
  journalctl -u "$SERVICE" -n 20 --no-pager
  die "Бот не запустился — смотри лог выше (часто это неверный BOT_TOKEN: исправь через  tbot config)"
fi

# ---------- проверка Binance ----------
step "Проверяю доступ к Binance с этого сервера"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 https://fapi.binance.com/fapi/v1/time || echo 000)"
if [[ "$code" == "200" ]]; then
  ok "Binance доступен"
  ip="$(curl -s -m 5 https://api.ipify.org || true)"
  [[ -n "$ip" ]] && echo "  IP сервера: $ip  (добавь его в API-ключ на account.tiger.com/api, если включена привязка к IP)"
else
  warn "Binance недоступен (код $code). Алерты будут работать, а импорт сделок — нет."
  warn "Нужен VPS в стране, где Binance разрешён (не США)."
fi

echo -e "\n${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
if [[ $UPDATING == 1 ]]; then
  echo -e "  ${G}Бот обновлён.${N}"
else
  echo -e "  ${G}Готово!${N} Открой своего бота в Telegram и нажми /start"
fi
echo "  Управление:  tbot status | logs | restart | update | config"
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}\n"
}

main "$@" </dev/null

# TimeTrack ssh wrapper — loguje ssh session (cíl + délku).
#
# Když se přihlásíš na klientský server, session trvá tak dlouho, jak na něm
# pracuješ — to je dobrý signál. reconcile.py to namapuje přes hostmap.
#
# Instalace — přidej do ~/.bashrc (za shell-logger.sh):
#     source /CESTA/K/office/tools/ssh-wrap.sh
#
# Ansible / scp / rsync přes ssh se řeší přes adresář projektu (viz dirmap),
# ne tady.

_TT_LOG_DIR="${TT_LOG_DIR:-$HOME/.local/share/timetrack}"
mkdir -p "$_TT_LOG_DIR" 2>/dev/null

ssh() {
  local start end ec
  start=$(date -Iseconds)
  command ssh "$@"
  ec=$?
  end=$(date -Iseconds)
  printf '%s\t%s\t%s\t%s\n' "$start" "$end" "$ec" "$*" >> "$_TT_LOG_DIR/ssh.log"
  return $ec
}

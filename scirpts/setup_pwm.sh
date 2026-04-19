#!/usr/bin/env bash
# ----------------------------------------------------------------------
# setup_pwm.sh  --  Production setup for PWM-based fsync trigger
#
# Installs a udev rule and a systemd oneshot service so that a given
# PWM channel works without root privileges.  Reusable across boards —
# pass --chip / --channel to override the defaults.
#
# sysfs quirks addressed:
#   - chown/chgrp silently ignored on sysfs attribute files (kernel)
#   - find -L loops infinitely via device/subsystem symlinks
#   => Solution: chmod a+rw on the exact attribute files we need.
#
# Usage:
#   sudo ./setup_pwm.sh                          # pwmchip3/pwm0 (RK3588 default)
#   sudo ./setup_pwm.sh --chip pwmchip0 --channel 1
#   sudo ./setup_pwm.sh --uninstall
#   sudo ./setup_pwm.sh <username>               # setup for a specific user
# ----------------------------------------------------------------------
set -euo pipefail

# ── defaults (RK3588 Nori fsync) ────────────────────────────────────
PWM_CHIP="pwmchip3"
PWM_CHAN="0"
ACTION="install"
TARGET_USER=""

UDEV_RULE="/etc/udev/rules.d/99-pwm-fsync.rules"
SYSTEMD_UNIT="/etc/systemd/system/pwm-fsync-setup.service"

# ── helpers ──────────────────────────────────────────────────────────
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }

need_root() {
    if [[ $EUID -ne 0 ]]; then
        red "This script must be run with sudo."
        exit 1
    fi
}

usage() {
    cat << EOF
Usage: sudo $0 [OPTIONS] [username]

Options:
  --chip CHIP        PWM chip name  (default: pwmchip3)
  --channel CHAN     PWM channel    (default: 0)
  --uninstall, -u    Remove udev rule and systemd service
  --help, -h         Show this help

Examples:
  sudo $0                                  # RK3588 default (pwmchip3/pwm0)
  sudo $0 --chip pwmchip0 --channel 1      # different board layout
  sudo $0 --uninstall
EOF
    exit 0
}

# Set permissions on the exact sysfs files that FsyncTrigger needs.
fix_permissions() {
    local chip_dir="/sys/class/pwm/$1"
    local chan_dir="${chip_dir}/pwm$2"

    chmod a+rw "${chip_dir}/export" "${chip_dir}/unexport" 2>/dev/null || true

    if [[ -d "$chan_dir" ]]; then
        chmod a+rw "${chan_dir}/period" \
                   "${chan_dir}/duty_cycle" \
                   "${chan_dir}/enable" \
                   "${chan_dir}/polarity" 2>/dev/null || true
    fi
}

# ── parse args ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --chip)      PWM_CHIP="$2"; shift 2 ;;
        --channel)   PWM_CHAN="$2";  shift 2 ;;
        --uninstall|-u) ACTION="uninstall"; shift ;;
        --help|-h)   usage ;;
        -*)          red "Unknown option: $1"; usage ;;
        *)           TARGET_USER="$1"; shift ;;
    esac
done

# ── uninstall ────────────────────────────────────────────────────────
do_uninstall() {
    need_root
    info "Stopping and disabling systemd service..."
    systemctl disable --now pwm-fsync-setup.service 2>/dev/null || true
    rm -f "$SYSTEMD_UNIT"
    systemctl daemon-reload

    info "Removing udev rule..."
    rm -f "$UDEV_RULE"
    udevadm control --reload-rules

    green "PWM fsync setup has been removed."
    exit 0
}

# ── install ──────────────────────────────────────────────────────────
do_install() {
    need_root

    local target_user="${TARGET_USER:-${SUDO_USER:-}}"
    if [[ -z "$target_user" ]]; then
        red "Cannot determine target user. Pass a username or run via sudo."
        exit 1
    fi

    info "Configuring PWM: ${PWM_CHIP} / channel ${PWM_CHAN}"

    # 1) udev rule — generic for ALL pwm chips/channels
    info "Installing udev rule -> $UDEV_RULE"
    cat > "$UDEV_RULE" << 'UDEV'
# Make PWM sysfs attributes world-writable for non-root camera trigger.
#
# sysfs does NOT support chown/chgrp on attribute files, and find -L
# loops via device/subsystem symlinks.  We chmod the exact files instead.
#
# Generic: fires for ANY pwm chip / channel, not just one board layout.

# When a PWM chip appears — open export/unexport:
SUBSYSTEM=="pwm", ACTION=="add", ATTR{npwm}!="", \
    RUN+="/bin/sh -c 'chmod a+rw /sys%p/export /sys%p/unexport 2>/dev/null || true'"

# When a channel is exported — open its control attributes:
SUBSYSTEM=="pwm", ACTION=="add", KERNEL=="pwm[0-9]*", \
    RUN+="/bin/sh -c 'chmod a+rw /sys%p/period /sys%p/duty_cycle /sys%p/enable /sys%p/polarity 2>/dev/null || true'"
UDEV

    udevadm control --reload-rules
    info "udev rules reloaded."

    # 2) systemd oneshot service — parameterised with chip/channel
    info "Installing systemd service -> $SYSTEMD_UNIT"
    cat > "$SYSTEMD_UNIT" << EOF
[Unit]
Description=Export PWM ${PWM_CHIP}/pwm${PWM_CHAN} for camera fsync and set permissions
After=sysinit.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c '\\
    chmod a+rw /sys/class/pwm/${PWM_CHIP}/export /sys/class/pwm/${PWM_CHIP}/unexport 2>/dev/null || true; \\
    echo ${PWM_CHAN} > /sys/class/pwm/${PWM_CHIP}/export 2>/dev/null || true; \\
    sleep 0.5; \\
    chmod a+rw /sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}/period \\
               /sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}/duty_cycle \\
               /sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}/enable \\
               /sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}/polarity 2>/dev/null || true'
ExecStop=/bin/sh -c '\\
    echo 0 > /sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}/enable 2>/dev/null; \\
    echo ${PWM_CHAN} > /sys/class/pwm/${PWM_CHIP}/unexport 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now pwm-fsync-setup.service
    info "systemd service enabled and started."

    # 3) Fix permissions right now (in case this is a re-run)
    fix_permissions "$PWM_CHIP" "$PWM_CHAN"

    # 4) Trigger udev for already-present devices
    udevadm trigger --subsystem-match=pwm --action=add 2>/dev/null || true

    # 5) Verify
    echo ""
    local chan_dir="/sys/class/pwm/${PWM_CHIP}/pwm${PWM_CHAN}"
    if [[ -d "$chan_dir" ]]; then
        green "PWM channel ${chan_dir} is exported."
        ls -l "${chan_dir}/period" "${chan_dir}/duty_cycle" "${chan_dir}/enable" 2>/dev/null

        if sudo -u "$target_user" test -w "${chan_dir}/period" 2>/dev/null; then
            green "Verified: user '$target_user' has write access to PWM."
        else
            red "Warning: write access check failed — try rebooting."
        fi
    else
        info "PWM channel not yet visible (may need reboot if hardware is not present)."
    fi

    echo ""
    green "Setup complete!"
}

# ── dispatch ─────────────────────────────────────────────────────────
case "$ACTION" in
    uninstall) do_uninstall ;;
    install)   do_install ;;
esac

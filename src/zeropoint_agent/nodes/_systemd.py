"""Shared systemd unit generation helper."""

MARKER_DIR = "/etc/zeropoint"
AGENT_BIN = "/usr/bin/zeropoint-agent"


def systemd_unit(node_id: str, description: str, parent_ids: list,
                 exec_start: str, exec_verify: str) -> str:
    """Generate a systemd oneshot unit for a DAG node."""
    after = "\n".join(f"After=zeropoint-{pid}.service" for pid in parent_ids)
    requires = "\n".join(f"Requires=zeropoint-{pid}.service" for pid in parent_ids)

    return f"""[Unit]
Description=ZeroPoint: {description}
{after}
{requires}
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={exec_start}
ExecStartPost=/bin/touch {MARKER_DIR}/.zeropoint-{node_id}

[Install]
WantedBy=multi-user.target
"""

"""Where the source systems live.

Contoso POS, Contoso Web and Contoso ERP are NOT Fabric. They are the vendors a
Fabric pipeline pulls from, and in production they are a real REST endpoint, a
real Postgres and a real Kafka broker. Keeping their addresses out of fabric.py
is the same discipline as keeping the emulator out of the platform: a Fabric
client should not know what a vendor's DSN looks like.

Every value is overridable, because that is the only thing that changes when
this platform is pointed at real vendors.
"""

from __future__ import annotations

import os

# Contoso POS — the vendor's export API. mokapi serves the seeded generator's
# bytes here; in production this is the vendor's own hostname.
POS_API = os.environ.get("POS_API_URL", "http://localhost:18090")
POS_API_KEY = os.environ.get("POS_API_KEY", "contoso-pos-key-7731-dev")

# Contoso ERP — a relational source, captured by CDC.
ERP_DSN = os.environ.get(
    "ERP_DSN", "postgresql://contoso:contoso-erp-dev@localhost:55432/erp"
)
DEBEZIUM = os.environ.get("DEBEZIUM_URL", "http://localhost:18083")
REDPANDA = os.environ.get("REDPANDA_BOOTSTRAP", "localhost:19092")
ERP_TOPIC = os.environ.get("ERP_TOPIC", "contoso.erp.customer")

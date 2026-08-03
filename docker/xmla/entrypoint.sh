#!/bin/sh
# Trust the stack's certificate the ordinary way.
#
# .NET verifies TLS chains and the local family serves a self-signed cert, so
# without this ADOMD fails with `UntrustedRoot` — which looks like an XMLA
# problem and is not one. Against real Fabric no certificate is mounted and this
# is a no-op, because a real chain already validates.
set -e
if [ -f /certs/emulator.crt ]; then
    cp /certs/emulator.crt /usr/local/share/ca-certificates/emulator.crt
    update-ca-certificates >/dev/null 2>&1
fi
exec dotnet run -c Release --no-build --

# A real BI client, on Linux. ADOMD.NET is what Power BI Desktop, DAX Studio and
# Tabular Editor use to reach a semantic model over XMLA — so a query that
# succeeds here is a query a BI tool can run.
FROM mcr.microsoft.com/dotnet/sdk:8.0
WORKDIR /probe
COPY docker/xmla/probe.csproj .
RUN dotnet restore
COPY docker/xmla/Program.cs .
RUN dotnet build -c Release --no-restore
COPY docker/xmla/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

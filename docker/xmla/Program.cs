// Run a REAL DAX query against the semantic model with a REAL BI client.
//
// Not a listener check. Power BI Desktop, DAX Studio and Tabular Editor all
// reach a semantic model through XMLA using this same ADOMD.NET client and the
// same connection-string token — so if this query returns the number our REST
// path returns, the model is genuinely queryable by a BI tool, and not merely
// by the code that published it.
//
// The token comes from entra-emulator (or real Entra) and is passed as
// `Password=`, which is what a headless client does.
//
// Exit codes are the contract, because the caller has to tell two very
// different outcomes apart:
//   0  the query ran — the value is printed as RESULT <n>
//   3  no XMLA surface at this target (expected today; docs/24 defers it)
//   2  something else went wrong, which is a finding rather than a state
using System;
using Microsoft.AnalysisServices.AdomdClient;

class Probe {
    static int Main() {
        var target = Environment.GetEnvironmentVariable("XMLA_TARGET");
        var token = Environment.GetEnvironmentVariable("XMLA_TOKEN");
        var ws = Environment.GetEnvironmentVariable("XMLA_WORKSPACE");
        var dataset = Environment.GetEnvironmentVariable("XMLA_DATASET");
        var dax = Environment.GetEnvironmentVariable("XMLA_QUERY");
        if (string.IsNullOrEmpty(target) || string.IsNullOrEmpty(token)
            || string.IsNullOrEmpty(ws) || string.IsNullOrEmpty(dax)) {
            Console.Error.WriteLine("XMLA_TARGET, XMLA_TOKEN, XMLA_WORKSPACE and XMLA_QUERY are required");
            return 2;
        }

        Console.WriteLine($"PLATFORM {System.Runtime.InteropServices.RuntimeInformation.OSDescription}");
        var cs = $"Data Source=powerbi://{target}/v1.0/myorg/{ws};User ID=;Password={token};"
               + (string.IsNullOrEmpty(dataset) ? "" : $"Initial Catalog={dataset};");

        try {
            using var conn = new AdomdConnection(cs);
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = dax;
            using var reader = cmd.ExecuteReader();
            double total = 0;
            int rows = 0;
            while (reader.Read()) {
                rows++;
                for (int i = 0; i < reader.FieldCount; i++) {
                    var v = reader.GetValue(i);
                    if (v is double d) total += d;
                    else if (v is decimal m) total += (double)m;
                }
            }
            Console.WriteLine($"ROWS {rows}");
            Console.WriteLine($"RESULT {total:F2}");
            return 0;
        } catch (Exception e) {
            // Walk the whole chain. ADOMD wraps the transport error in a
            // generic "PowerBI Request Failed", and the HTTP status that
            // actually distinguishes "no endpoint here" from "auth rejected"
            // or "protocol error" is several levels down.
            var chain = new System.Text.StringBuilder();
            for (Exception x = e; x != null; x = x.InnerException) {
                var m = x.Message.Split('\n')[0].Trim();
                Console.WriteLine($"FAILED {x.GetType().Name} :: {m}");
                chain.Append(m).Append(' ');
            }
            var first = chain.ToString();
            // A refused or unanswered connection is "no XMLA surface here",
            // which is the documented state today. Anything else — a protocol
            // error, an auth rejection, a malformed response — means something
            // IS there and behaving unexpectedly, and that is worth failing on.
            // "No XMLA surface" covers two shapes: nothing listening at all,
            // and something listening that does not serve XMLA (the emulator
            // answers 404/405 on the path ADOMD posts to). Auth rejection and
            // protocol errors are deliberately NOT in this set — those mean a
            // surface exists and is misbehaving, which is a finding.
            var noSurface = first.Contains("refused", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("Unable to connect", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("No connection could be made", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("could not be resolved", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("404", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("405", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("Not Found", StringComparison.OrdinalIgnoreCase)
                         || first.Contains("Method Not Allowed", StringComparison.OrdinalIgnoreCase)
                         // ADOMD's FIRST call is Power BI premium workspace
                         // discovery, and it expects JSON. A host that serves
                         // something else fails to deserialize before any XMLA
                         // is spoken — which is "no XMLA surface here", stated
                         // in the client's own vocabulary.
                         || (first.Contains("deserializing", StringComparison.OrdinalIgnoreCase)
                             && first.Contains("PbiPremiumAuthenticationHandle", StringComparison.OrdinalIgnoreCase));
            return noSurface ? 3 : 2;
        }
    }
}

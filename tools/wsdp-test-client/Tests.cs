using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Xml;
using System.Xml.Linq;

namespace WsdpTestClient
{
    internal static class Tests
    {
        private static int passed;

        [STAThread]
        private static int Main(string[] args)
        {
            try
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                Run("SOAP namespaces, XML escaping, PasswordText and nonce", Envelope);
                Run("Request preview does not contain a password", Preview);
                Run("HTTP 200 success and inherited common namespace", Success);
                Run("Application error in HTTP 200", ApplicationError);
                Run("SOAP authentication fault in HTTP 500", Fault);
                Run("HTTP errors, wrong response and missing result", UnexpectedResponses);
                Run("DTD/external entities and oversized XML rejected", XmlSecurity);
                Run("Reflected credentials are redacted", Redaction);
                Run("Correct endpoint, SOAPAction and UTF-8 transport", delegate { Transport().GetAwaiter().GetResult(); });
                Run("Response size limit, including missing Content-Length", delegate { SizeLimits().GetAwaiter().GetResult(); });
                Run("Cancellation and timeout", delegate { Cancellation().GetAwaiter().GetResult(); });
                Run("WinForms layout, initial fields and render", FormSmoke);
                if (args.Contains("--live")) Run("Public CUZK trial: three operations and invalid login", delegate { Live().GetAwaiter().GetResult(); });
                Console.WriteLine("PASS: " + passed + " test groups.");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("FAIL: " + error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        private static void Run(string name, Action test)
        {
            test();
            passed++;
            Console.WriteLine("PASS " + name);
        }

        private static void Check(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }

        private static string Reply(WsdpOperation operation, string level, string code)
        {
            XNamespace ns = operation == WsdpOperation.AccountStatus ? WsdpClient.Common : operation.Namespace;
            return new XDocument(new XElement(WsdpClient.Soap + "Envelope",
                new XElement(WsdpClient.Soap + "Body",
                    new XElement(operation.Namespace + (operation.ElementPrefix + "Response"),
                        new XElement(ns + "vysledek", new XElement(WsdpClient.Common + "zprava",
                            new XAttribute("kod", code), new XAttribute("uroven", level), "Test response")),
                        operation == WsdpOperation.AccountStatus ? new XElement(WsdpClient.Common + "stavSluzby",
                            new XElement(WsdpClient.Common + "verze", "3.1"),
                            new XElement(WsdpClient.Common + "prihlasovaciJmeno", "demo")) : null)))).ToString();
        }

        private static void Envelope()
        {
            const string secret = "p<&\"' ř";
            var xml = WsdpClient.CreateEnvelope(WsdpOperation.AccountStatus, "u<& ř", secret);
            var parsed = WsdpClient.ParseXml(xml.ToString());
            Check(parsed.Root.Name == WsdpClient.Soap + "Envelope", "SOAP 1.1 required.");
            Check(parsed.Descendants(WsdpClient.Security + "Username").Single().Value == "u<& ř", "Username escaped incorrectly.");
            var password = parsed.Descendants(WsdpClient.Security + "Password").Single();
            Check(password.Value == secret && ((string)password.Attribute("Type")).EndsWith("#PasswordText"), "PasswordText incorrect.");
            Check(parsed.Root.Element(WsdpClient.Soap + "Body").Elements().Single().Name == WsdpOperation.AccountStatus.Namespace + "StavWSRequest", "Wrong request element.");
            var nonce = parsed.Descendants(WsdpClient.Security + "Nonce").Single().Value;
            Check(Convert.FromBase64String(nonce).Length == 16, "Invalid nonce.");
            var next = WsdpClient.CreateEnvelope(WsdpOperation.AccountStatus, "u", "p");
            Check(nonce != next.Descendants(WsdpClient.Security + "Nonce").Single().Value, "Nonce reused.");
            Check(parsed.Descendants(WsdpClient.Utility + "Created").Single().Value.EndsWith("Z"), "Created is not UTC.");
        }

        private static void Preview()
        {
            var xml = WsdpClient.ParseXml(WsdpClient.RequestPreview(WsdpOperation.Regions, "demo"));
            Check(xml.Descendants(WsdpClient.Security + "Password").Single().Value == "[HESLO SKRYTO]", "Password exposed.");
        }

        private static void Success()
        {
            var result = WsdpClient.ParseResponse(200, Reply(WsdpOperation.AccountStatus, "INFORMACE", "0"), WsdpOperation.AccountStatus, "secret");
            Check(result.Success && result.Summary.Contains("demo") && result.Summary.Contains("3.1"), "Common namespace not parsed.");
            result = WsdpClient.ParseResponse(200, Reply(WsdpOperation.Regions, "VAROVANI", "9001"), WsdpOperation.Regions, "secret");
            Check(result.Success && result.HasWarnings, "Warning should remain visible.");
            result = WsdpClient.ParseResponse(200, Reply(WsdpOperation.Regions, "INFORMACE", "0"), WsdpOperation.Regions, "INFORMACE");
            Check(result.Success, "Diagnostic redaction must not alter result classification.");
        }

        private static void ApplicationError()
        {
            var result = WsdpClient.ParseResponse(200, Reply(WsdpOperation.Regions, "CHYBA", "385"), WsdpOperation.Regions, "secret");
            Check(!result.Success && result.Summary.Contains("385"), "Application error treated as success.");
        }

        private static void Fault()
        {
            const string body = "<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/'><s:Body><s:Fault><faultcode>wsse:FailedAuthentication</faultcode><faultstring>Invalid credentials</faultstring></s:Fault></s:Body></s:Envelope>";
            var result = WsdpClient.ParseResponse(500, body, WsdpOperation.AccountStatus, "secret");
            Check(!result.Success && result.Summary.Contains("FailedAuthentication") && result.Summary.Contains("IP"), "SOAP fault not diagnosed.");
        }

        private static void UnexpectedResponses()
        {
            Check(!WsdpClient.ParseResponse(503, Reply(WsdpOperation.Regions, "INFORMACE", "0"), WsdpOperation.Regions, "secret").Success, "HTTP 503 treated as success.");
            Check(!WsdpClient.ParseResponse(200, Reply(WsdpOperation.Regions, "INFORMACE", "0"), WsdpOperation.AccountStatus, "secret").Success, "Wrong response accepted.");
            Check(!WsdpClient.ParseResponse(200, "<html>proxy failure</html>", WsdpOperation.Regions, "secret").Success, "HTML accepted.");
            var xml = WsdpClient.ParseXml(Reply(WsdpOperation.Regions, "INFORMACE", "0"));
            xml.Descendants(WsdpClient.Common + "zprava").Remove();
            Check(!WsdpClient.ParseResponse(200, xml.ToString(), WsdpOperation.Regions, "secret").Success, "Missing result accepted.");
        }

        private static void XmlSecurity()
        {
            Expect<XmlException>(delegate { WsdpClient.ParseXml("<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///nonexistent'>]><x>&e;</x>"); });
            Expect<XmlException>(delegate { WsdpClient.ParseXml("<x>" + new string('a', WsdpClient.MaxResponseBytes + 1) + "</x>"); });
            var result = WsdpClient.ParseResponse(502, "bad password secret", WsdpOperation.Regions, "secret");
            Check(!result.ResponseXml.Contains("secret"), "Unsafe non-XML body exposed.");
        }

        private static void Redaction()
        {
            var xml = WsdpClient.ParseXml(Reply(WsdpOperation.Regions, "CHYBA", "385"));
            xml.Root.AddFirst(new XElement(WsdpClient.Soap + "Header", new XElement(WsdpClient.Security + "Password", "p<&ř")));
            xml.Descendants(WsdpClient.Common + "zprava").Single().Value = "Reflected p<&ř";
            var result = WsdpClient.ParseResponse(200, xml.ToString(), WsdpOperation.Regions, "p<&ř");
            Check(!result.ResponseXml.Contains("p&lt;&amp;ř") && !result.Summary.Contains("p<&ř"), "Reflected password exposed.");
            WsdpClient.ParseXml(result.ResponseXml);
        }

        private static async Task Transport()
        {
            using (var client = new WsdpClient(new FakeHandler(async delegate(HttpRequestMessage request, CancellationToken token)
            {
                Check(request.RequestUri.AbsoluteUri == "https://wsdptrial.cuzk.gov.cz/trial/ws/wsdp/3.1/ucet", "Wrong trial URL.");
                Check(request.Method == HttpMethod.Post, "Expected POST.");
                Check(request.Headers.GetValues("SOAPAction").Single() == "\"urn:cz:gov:cuzk:iskn:services:wsdp:ucet:stavWS\"", "Wrong SOAPAction.");
                Check(request.Content.Headers.ContentType.MediaType == "text/xml" && request.Content.Headers.ContentType.CharSet == "utf-8", "Wrong content type.");
                Check(WsdpClient.ParseXml(await request.Content.ReadAsStringAsync()).Descendants(WsdpClient.Security + "Password").Single().Value == "synthetic-password", "Password missing in actual request.");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(Reply(WsdpOperation.AccountStatus, "INFORMACE", "0")) };
            })))
            {
                var result = await client.CallAsync(WsdpEnvironment.Trial, WsdpOperation.AccountStatus, "demo", "synthetic-password", TimeSpan.FromSeconds(5), CancellationToken.None);
                Check(result.Success, "Mock transport failed.");
            }
            Check(WsdpOperation.Regions.Endpoint(WsdpEnvironment.Production).AbsoluteUri == "https://katastr.cuzk.gov.cz/ws/wsdp/3.1/ciselnik", "Wrong production URL.");
        }

        private static async Task SizeLimits()
        {
            foreach (bool unknownLength in new[] { false, true })
            {
                using (var client = new WsdpClient(new FakeHandler(delegate(HttpRequestMessage request, CancellationToken token)
                {
                    HttpContent content = unknownLength
                        ? (HttpContent)new UnknownLengthContent(new byte[WsdpClient.MaxResponseBytes + 1])
                        : new ByteArrayContent(new byte[WsdpClient.MaxResponseBytes + 1]);
                    return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
                })))
                {
                    bool blocked = false;
                    try { await client.CallAsync(WsdpEnvironment.Trial, WsdpOperation.AccountStatus, "demo", "secret", TimeSpan.FromSeconds(5), CancellationToken.None); }
                    catch (InvalidDataException) { blocked = true; }
                    Check(blocked, "Oversized response accepted.");
                }
            }
        }

        private static async Task Cancellation()
        {
            using (var client = new WsdpClient(new FakeHandler(async delegate(HttpRequestMessage request, CancellationToken token)
            {
                await Task.Delay(10000, token);
                return new HttpResponseMessage(HttpStatusCode.OK);
            })))
            using (var cancelled = new CancellationTokenSource())
            {
                cancelled.Cancel();
                bool blocked = false;
                try { await client.CallAsync(WsdpEnvironment.Trial, WsdpOperation.AccountStatus, "demo", "secret", TimeSpan.FromSeconds(5), cancelled.Token); }
                catch (OperationCanceledException) { blocked = true; }
                Check(blocked, "Cancellation ignored.");
                blocked = false;
                try { await client.CallAsync(WsdpEnvironment.Trial, WsdpOperation.AccountStatus, "demo", "secret", TimeSpan.FromMilliseconds(30), CancellationToken.None); }
                catch (OperationCanceledException) { blocked = true; }
                Check(blocked, "Timeout ignored.");
            }
        }

        private static void FormSmoke()
        {
            Application.EnableVisualStyles();
            using (var form = new MainForm())
            {
                form.ShowInTaskbar = false;
                form.StartPosition = FormStartPosition.Manual;
                form.Location = new Point(-10000, -10000);
                form.Show();
                Application.DoEvents();
                form.PerformLayout();
                Check(form.Text.Contains("WSDP"), "Window title missing.");
                var controls = Children(form).ToList();
                var combos = controls.OfType<ComboBox>().ToList();
                Check(combos.Count == 2 && combos.All(c => c.SelectedIndex == 0), "Initial selections incorrect.");
                var secret = controls.OfType<TextBox>().Single(c => c.UseSystemPasswordChar);
                Check(secret.Text == "", "Password must start empty.");
                controls.OfType<Button>().Single(b => b.Text.StartsWith("Vyplnit veřejný")).PerformClick();
                Check(secret.Text == "WSHESLO", "Public trial fill button failed.");
                combos.Single(c => c.Items.Count == 2).SelectedIndex = 1;
                Check(secret.Text == "", "Password leaked across environments.");
                combos.Single(c => c.Items.Count == 2).SelectedIndex = 0;
                using (var bitmap = new Bitmap(form.Width, form.Height))
                {
                    form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, form.Size));
                    string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ui-preview.png");
                    bitmap.Save(path);
                }
                form.Hide();
            }
            // This console runner has no WinForms message loop for subsequent async tests.
            SynchronizationContext.SetSynchronizationContext(null);
        }

        private static System.Collections.Generic.IEnumerable<Control> Children(Control parent)
        {
            foreach (Control child in parent.Controls)
            {
                yield return child;
                foreach (var descendant in Children(child)) yield return descendant;
            }
        }

        private static async Task Live()
        {
            using (var client = new WsdpClient())
            {
                foreach (var operation in new[] { WsdpOperation.AccountStatus, WsdpOperation.Regions, WsdpOperation.CodeLists })
                {
                    // Public demo credentials, published by CUZK. Never use a real account in automated tests.
                    var result = await client.CallAsync(WsdpEnvironment.Trial, operation, "WSTEST", "WSHESLO", TimeSpan.FromSeconds(30), CancellationToken.None);
                    Console.WriteLine(operation.Action + ": " + result.Summary);
                    Check(result.Success, "Trial service did not return success for " + operation.Action);
                }
                var invalid = await client.CallAsync(WsdpEnvironment.Trial, WsdpOperation.AccountStatus,
                    "WSTEST-NONEXISTENT-TEST-ACCOUNT", "invalid-synthetic-password", TimeSpan.FromSeconds(30), CancellationToken.None);
                Check(!invalid.Success && invalid.Summary.Contains("FailedAuthentication"), "Invalid login not rejected as expected.");
                Console.WriteLine("Invalid trial login: correctly rejected.");
            }
        }

        private static void Expect<T>(Action action) where T : Exception
        {
            try { action(); }
            catch (T) { return; }
            throw new InvalidOperationException("Expected " + typeof(T).Name);
        }

        private sealed class FakeHandler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send;
            public FakeHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> send) { this.send = send; }
            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) { return send(request, token); }
        }

        private sealed class UnknownLengthContent : HttpContent
        {
            private readonly byte[] bytes;
            public UnknownLengthContent(byte[] bytes) { this.bytes = bytes; }
            protected override bool TryComputeLength(out long length) { length = 0; return false; }
            protected override Task SerializeToStreamAsync(Stream stream, TransportContext context) { return stream.WriteAsync(bytes, 0, bytes.Length); }
            protected override Task<Stream> CreateContentReadStreamAsync() { return Task.FromResult<Stream>(new MemoryStream(bytes)); }
        }
    }
}

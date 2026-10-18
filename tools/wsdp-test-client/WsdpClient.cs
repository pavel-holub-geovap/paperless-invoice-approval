using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Xml;
using System.Xml.Linq;

namespace WsdpTestClient
{
    public enum WsdpEnvironment { Trial, Production }

    public sealed class WsdpOperation
    {
        public static readonly WsdpOperation AccountStatus =
            new WsdpOperation("Ověřit přihlášení (stavWS)", "ucet", "stavWS", "StavWS");
        public static readonly WsdpOperation Regions =
            new WsdpOperation("Seznam krajů (seznamKraju)", "ciselnik", "seznamKraju", "SeznamKraju");
        public static readonly WsdpOperation CodeLists =
            new WsdpOperation("Seznam číselníků (seznamCiselniku)", "ciselnik", "seznamCiselniku", "SeznamCiselniku");

        public string Label { get; private set; }
        public string Service { get; private set; }
        public string Action { get; private set; }
        public string ElementPrefix { get; private set; }
        public XNamespace Namespace { get { return "urn:cz:gov:cuzk:iskn:types:wsdp:" + Service + ":3.1"; } }
        public string SoapAction { get { return "urn:cz:gov:cuzk:iskn:services:wsdp:" + Service + ":" + Action; } }

        private WsdpOperation(string label, string service, string action, string elementPrefix)
        {
            Label = label;
            Service = service;
            Action = action;
            ElementPrefix = elementPrefix;
        }

        public override string ToString() { return Label; }

        public Uri Endpoint(WsdpEnvironment environment)
        {
            if (environment == WsdpEnvironment.Trial)
                return new Uri("https://wsdptrial.cuzk.gov.cz/trial/ws/wsdp/3.1/" + Service);
            if (environment == WsdpEnvironment.Production)
                return new Uri("https://katastr.cuzk.gov.cz/ws/wsdp/3.1/" + Service);
            throw new ArgumentOutOfRangeException("environment");
        }
    }

    public sealed class WsdpResult
    {
        public int HttpStatus { get; internal set; }
        public bool Success { get; internal set; }
        public bool HasWarnings { get; internal set; }
        public string Summary { get; internal set; }
        public string ResponseXml { get; internal set; }
    }

    public sealed class WsdpClient : IDisposable
    {
        public const int MaxResponseBytes = 2 * 1024 * 1024;
        public static readonly XNamespace Soap = "http://schemas.xmlsoap.org/soap/envelope/";
        public static readonly XNamespace Security = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd";
        public static readonly XNamespace Utility = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd";
        public static readonly XNamespace Common = "urn:cz:gov:cuzk:iskn:types:common:3.1";
        private readonly HttpClient http;

        public WsdpClient() : this(new HttpClientHandler
        {
            AllowAutoRedirect = false,
            UseCookies = false,
            UseDefaultCredentials = false,
            AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate
        }) { }

        // Handler injection allows transport tests without real credentials or network traffic.
        public WsdpClient(HttpMessageHandler handler)
        {
            http = new HttpClient(handler);
            http.Timeout = Timeout.InfiniteTimeSpan;
        }

        public static XDocument CreateEnvelope(WsdpOperation operation, string username, string password)
        {
            if (operation == null) throw new ArgumentNullException("operation");
            if (string.IsNullOrWhiteSpace(username)) throw new ArgumentException("Vyplňte uživatelské jméno.");
            if (string.IsNullOrEmpty(password)) throw new ArgumentException("Vyplňte heslo.");
            byte[] nonce = new byte[16];
            using (var random = RandomNumberGenerator.Create()) random.GetBytes(nonce);
            return new XDocument(new XElement(Soap + "Envelope",
                new XAttribute(XNamespace.Xmlns + "soapenv", Soap),
                new XAttribute(XNamespace.Xmlns + "wsse", Security),
                new XAttribute(XNamespace.Xmlns + "wsu", Utility),
                new XAttribute(XNamespace.Xmlns + "wsdp", operation.Namespace),
                new XElement(Soap + "Header",
                    new XElement(Security + "Security", new XAttribute(Soap + "mustUnderstand", "1"),
                        new XElement(Security + "UsernameToken",
                            new XAttribute(Utility + "Id", "UsernameToken-" + Guid.NewGuid().ToString("D")),
                            new XElement(Security + "Username", username),
                            new XElement(Security + "Password", new XAttribute("Type",
                                "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText"), password),
                            new XElement(Security + "Nonce", new XAttribute("EncodingType",
                                "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"),
                                Convert.ToBase64String(nonce)),
                            new XElement(Utility + "Created", DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture))))),
                new XElement(Soap + "Body", new XElement(operation.Namespace + (operation.ElementPrefix + "Request")))));
        }

        public static string RequestPreview(WsdpOperation operation, string username)
        {
            return CreateEnvelope(operation, string.IsNullOrWhiteSpace(username) ? "[jméno]" : username,
                "[HESLO SKRYTO]").ToString();
        }

        public async Task<WsdpResult> CallAsync(WsdpEnvironment environment, WsdpOperation operation,
            string username, string password, TimeSpan timeout, CancellationToken cancellationToken)
        {
            if (timeout <= TimeSpan.Zero || timeout > TimeSpan.FromMinutes(5))
                throw new ArgumentOutOfRangeException("timeout");
            var envelope = CreateEnvelope(operation, username, password);
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
            using (var request = new HttpRequestMessage(HttpMethod.Post, operation.Endpoint(environment)))
            {
                deadline.CancelAfter(timeout);
                request.Headers.Add("SOAPAction", "\"" + operation.SoapAction + "\"");
                request.Headers.Accept.ParseAdd("text/xml");
                request.Content = new StringContent(envelope.ToString(SaveOptions.DisableFormatting), Encoding.UTF8, "text/xml");
                using (var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token).ConfigureAwait(false))
                {
                    string body = await ReadBoundedAsync(response.Content, deadline.Token).ConfigureAwait(false);
                    return ParseResponse((int)response.StatusCode, body, operation, password);
                }
            }
        }

        private static async Task<string> ReadBoundedAsync(HttpContent content, CancellationToken token)
        {
            if (content == null) return "";
            if (content.Headers.ContentLength > MaxResponseBytes)
                throw new InvalidDataException("Odpověď překročila limit 2 MiB.");
            using (var stream = await content.ReadAsStreamAsync().ConfigureAwait(false))
            using (token.Register(stream.Dispose))
            using (var buffer = new MemoryStream())
            {
                try
                {
                    byte[] block = new byte[8192];
                    int read;
                    while ((read = await stream.ReadAsync(block, 0, block.Length, token).ConfigureAwait(false)) > 0)
                    {
                        token.ThrowIfCancellationRequested();
                        if (buffer.Length + read > MaxResponseBytes)
                            throw new InvalidDataException("Odpověď překročila limit 2 MiB.");
                        buffer.Write(block, 0, read);
                    }
                    token.ThrowIfCancellationRequested();
                    // WSDP XML is UTF-8; StreamReader also handles a BOM if present.
                    buffer.Position = 0;
                    using (var reader = new StreamReader(buffer, Encoding.UTF8, true)) return reader.ReadToEnd();
                }
                catch (Exception)
                {
                    token.ThrowIfCancellationRequested();
                    throw;
                }
            }
        }

        public static XDocument ParseXml(string xml)
        {
            var settings = new XmlReaderSettings
            {
                DtdProcessing = DtdProcessing.Prohibit,
                XmlResolver = null,
                MaxCharactersInDocument = MaxResponseBytes,
                MaxCharactersFromEntities = 1024
            };
            using (var input = new StringReader(xml))
            using (var reader = XmlReader.Create(input, settings)) return XDocument.Load(reader);
        }

        public static WsdpResult ParseResponse(int httpStatus, string body, WsdpOperation operation, string password)
        {
            var result = new WsdpResult { HttpStatus = httpStatus, Success = false };
            XDocument document;
            try { document = ParseXml(body); }
            catch (XmlException)
            {
                // Do not display arbitrary HTML/plain text that could echo a request with credentials.
                result.ResponseXml = "Odpověď není bezpečné SOAP XML (nebo obsahuje zakázané DTD).";
                result.Summary = "HTTP " + httpStatus + ": neplatná XML odpověď. Zkontrolujte dostupnost služby/proxy.";
                return result;
            }

            // Parse the original values; redaction must not change result codes or message levels.
            result.ResponseXml = RedactedXml(document, password);
            var soapBody = document.Root == null || document.Root.Name != Soap + "Envelope"
                ? null : document.Root.Element(Soap + "Body");
            if (soapBody == null)
            {
                result.Summary = "HTTP " + httpStatus + ": odpověď není SOAP 1.1 Envelope/Body.";
                return result;
            }
            var fault = soapBody.Element(Soap + "Fault");
            if (fault != null)
            {
                string code = (string)fault.Element("faultcode") ?? "Fault";
                string message = (string)fault.Element("faultstring") ?? "SOAP chyba";
                result.Summary = "SOAP " + code + ": " + message;
                if (code.IndexOf("FailedAuthentication", StringComparison.OrdinalIgnoreCase) >= 0)
                    result.Summary += "\r\nOvěřte jméno, heslo, prostředí, oprávnění WSDP a případné omezení IP účtu.";
                result.Summary = Mask(result.Summary, password);
                return result;
            }
            var payload = soapBody.Element(operation.Namespace + (operation.ElementPrefix + "Response"));
            if (payload == null || soapBody.Elements().Count() != 1)
            {
                result.Summary = "HTTP " + httpStatus + ": neočekávaný typ odpovědi WSDP.";
                return result;
            }
            // stavWS inherits its children from commonTypes; list operations declare their own.
            XNamespace outcomeNamespace = operation == WsdpOperation.AccountStatus ? Common : operation.Namespace;
            var outcome = payload.Element(outcomeNamespace + "vysledek");
            var messages = outcome == null ? new List<XElement>() : outcome.Elements(Common + "zprava").ToList();
            bool hasError = messages.Any(m => (string)m.Attribute("uroven") == "CHYBA");
            bool validMessages = messages.Count > 0 && messages.All(m =>
                m.Attribute("kod") != null && new[] { "INFORMACE", "VAROVANI", "CHYBA" }.Contains((string)m.Attribute("uroven")));
            result.HasWarnings = messages.Any(m => (string)m.Attribute("uroven") == "VAROVANI");
            result.Success = httpStatus >= 200 && httpStatus < 300 && validMessages && !hasError;
            result.Summary = "HTTP " + httpStatus + (result.Success ? " • Volání proběhlo úspěšně." : " • Volání skončilo chybou.");
            if (!validMessages) result.Summary += "\r\nChybí platný výsledek operace WSDP.";
            foreach (var message in messages)
                result.Summary += "\r\n" + (string)message.Attribute("uroven") + " [" + (string)message.Attribute("kod") + "]: " + message.Value;
            var state = payload.Element(Common + "stavSluzby");
            if (state != null)
            {
                var login = state.Element(Common + "prihlasovaciJmeno");
                var version = state.Element(Common + "verze");
                if (login != null) result.Summary += "\r\nPřihlášený uživatel: " + login.Value;
                if (version != null) result.Summary += "\r\nVerze služby: " + version.Value;
            }
            result.Summary = Mask(result.Summary, password);
            return result;
        }

        private static string Mask(string value, string password)
        {
            return string.IsNullOrEmpty(password) ? value : value.Replace(password, "[HESLO SKRYTO]");
        }

        private static string RedactedXml(XDocument document, string password)
        {
            var display = new XDocument(document);
            foreach (var secret in display.Descendants(Security + "Password")) secret.Value = "[HESLO SKRYTO]";
            foreach (var text in display.DescendantNodes().OfType<XText>()) text.Value = Mask(text.Value, password);
            foreach (var attribute in display.Descendants().Attributes().Where(a => !a.IsNamespaceDeclaration))
                attribute.Value = Mask(attribute.Value, password);
            foreach (var comment in display.DescendantNodes().OfType<XComment>()) comment.Value = "[KOMENTÁŘ SKRYT]";
            foreach (var instruction in display.DescendantNodes().OfType<XProcessingInstruction>()) instruction.Remove();
            return display.ToString();
        }

        public void Dispose() { http.Dispose(); }
    }
}

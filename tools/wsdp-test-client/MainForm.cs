using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace WsdpTestClient
{
    public sealed class MainForm : Form
    {
        private readonly WsdpClient client = new WsdpClient();
        private readonly ComboBox environment = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill };
        private readonly ComboBox operation = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill };
        private readonly TextBox username = new TextBox { Dock = DockStyle.Fill, MaxLength = 256 };
        private readonly TextBox password = new TextBox { Dock = DockStyle.Fill, UseSystemPasswordChar = true, MaxLength = 1024 };
        private readonly TextBox endpoint = new TextBox { Dock = DockStyle.Fill, ReadOnly = true };
        private readonly NumericUpDown timeout = new NumericUpDown { Minimum = 5, Maximum = 300, Value = 30, Width = 65 };
        private readonly Button send = new Button { Text = "Zavolat WSDP", AutoSize = true, Padding = new Padding(12, 5, 12, 5) };
        private readonly Button cancel = new Button { Text = "Zrušit volání", AutoSize = true, Enabled = false, Padding = new Padding(8, 5, 8, 5) };
        private readonly Button demo = new Button { Text = "Vyplnit veřejný testovací účet", AutoSize = true };
        private readonly Button save = new Button { Text = "Uložit odpověď…", AutoSize = true, Enabled = false };
        private readonly CheckBox showPassword = new CheckBox { Text = "Zobrazit heslo", AutoSize = true };
        private readonly TextBox requestText = XmlBox();
        private readonly TextBox responseText = XmlBox();
        private readonly TextBox summary = new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill, ScrollBars = ScrollBars.Vertical };
        private readonly Label status = new Label { AutoSize = true, Text = "Připraveno. Zadejte jméno a heslo nebo použijte veřejný testovací účet.", Dock = DockStyle.Fill };
        private readonly TabControl tabs = new TabControl { Dock = DockStyle.Fill };
        private CancellationTokenSource activeCall;

        public MainForm()
        {
            Text = "WSDP 3.1 • Testovací klient ČÚZK";
            Font = new Font("Segoe UI", 10F);
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(1080, 800);
            MinimumSize = new Size(820, 660);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(245, 247, 250);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22), ColumnCount = 1, RowCount = 7 };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 202));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 53));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 110));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
            Controls.Add(layout);
            layout.Controls.Add(new Label { Text = "WSDP / test připojení", AutoSize = true,
                Font = new Font("Segoe UI", 18F, FontStyle.Bold), ForeColor = Color.FromArgb(25, 50, 80) }, 0, 0);
            layout.Controls.Add(new Label { Text = "SOAP 1.1 • WS-Security UsernameToken • Heslo se neukládá.\r\nZačněte ověřením přihlášení (stavWS); další volby načítají číselníky.", AutoSize = true }, 0, 1);

            var fields = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 5 };
            fields.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 155));
            fields.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 55));
            fields.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 150));
            fields.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 45));
            for (int row = 0; row < 5; row++) fields.RowStyles.Add(new RowStyle(SizeType.Percent, 20));
            AddField(fields, "Prostředí", environment, 0, 0);
            AddField(fields, "Časový limit (s)", timeout, 2, 0);
            AddField(fields, "Uživatelské jméno", username, 0, 1);
            fields.Controls.Add(demo, 2, 1);
            fields.SetColumnSpan(demo, 2);
            AddField(fields, "Heslo", password, 0, 2);
            fields.Controls.Add(showPassword, 2, 2);
            fields.SetColumnSpan(showPassword, 2);
            AddField(fields, "Operace", operation, 0, 3);
            fields.SetColumnSpan(operation, 3);
            AddField(fields, "Endpoint", endpoint, 0, 4);
            fields.SetColumnSpan(endpoint, 3);
            layout.Controls.Add(fields, 0, 2);

            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
            actions.Controls.AddRange(new Control[] { send, cancel, save });
            layout.Controls.Add(actions, 0, 3);
            layout.Controls.Add(summary, 0, 4);
            AddTab("Odpověď služby", responseText);
            AddTab("SOAP požadavek (heslo skryto)", requestText);
            layout.Controls.Add(tabs, 0, 5);
            layout.Controls.Add(status, 0, 6);

            environment.Items.AddRange(new object[] { "Na zkoušku (fiktivní data)", "Produkce (vlastní účet WSDP)" });
            operation.Items.AddRange(new object[] { WsdpOperation.AccountStatus, WsdpOperation.Regions, WsdpOperation.CodeLists });
            environment.SelectedIndexChanged += delegate
            {
                // Never carry credentials from one environment into the other.
                username.Clear();
                password.Clear();
                demo.Enabled = environment.SelectedIndex == 0;
                UpdatePreview();
            };
            operation.SelectedIndexChanged += delegate { UpdatePreview(); };
            username.TextChanged += delegate { UpdatePreview(); };
            showPassword.CheckedChanged += delegate { password.UseSystemPasswordChar = !showPassword.Checked; };
            demo.Click += delegate { username.Text = "WSTEST"; password.Text = "WSHESLO"; };
            send.Click += SendAsync;
            cancel.Click += delegate { if (activeCall != null) activeCall.Cancel(); };
            save.Click += SaveResponse;
            environment.SelectedIndex = 0;
            operation.SelectedIndex = 0;
            summary.Text = "1. Vyplňte účet pro zvolené prostředí.\r\n2. Vyberte operaci a klikněte na Zavolat WSDP.\r\n3. Zde uvidíte výsledek; dole je XML odpověď a náhled požadavku.";
            AcceptButton = send;
            FormClosing += delegate { if (activeCall != null) activeCall.Cancel(); password.Clear(); };
        }

        private static TextBox XmlBox()
        {
            return new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill,
                ScrollBars = ScrollBars.Both, WordWrap = false, MaxLength = 0,
                Font = new Font("Consolas", 10F), BackColor = Color.White };
        }

        private static void AddField(TableLayoutPanel panel, string title, Control control, int column, int row)
        {
            panel.Controls.Add(new Label { Text = title, AutoSize = true, Anchor = AnchorStyles.Left }, column, row);
            control.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            control.Margin = new Padding(3, 5, 14, 5);
            panel.Controls.Add(control, column + 1, row);
        }

        private void AddTab(string title, Control content)
        {
            var page = new TabPage(title) { Padding = new Padding(8) };
            page.Controls.Add(content);
            tabs.TabPages.Add(page);
        }

        private WsdpEnvironment SelectedEnvironment
        {
            get { return environment.SelectedIndex == 0 ? WsdpEnvironment.Trial : WsdpEnvironment.Production; }
        }

        private void UpdatePreview()
        {
            var selected = operation.SelectedItem as WsdpOperation;
            if (selected == null) return;
            endpoint.Text = selected.Endpoint(SelectedEnvironment).AbsoluteUri;
            try { requestText.Text = WsdpClient.RequestPreview(selected, username.Text.Trim()); }
            catch (ArgumentException) { requestText.Text = "Uživatelské jméno obsahuje znak nepovolený v XML."; }
        }

        private void SetBusy(bool busy)
        {
            environment.Enabled = operation.Enabled = username.Enabled = password.Enabled = timeout.Enabled = !busy;
            demo.Enabled = !busy && environment.SelectedIndex == 0;
            send.Enabled = !busy;
            cancel.Enabled = busy;
            showPassword.Checked = false;
            showPassword.Enabled = !busy;
            UseWaitCursor = busy;
        }

        private async void SendAsync(object sender, EventArgs args)
        {
            if (string.IsNullOrWhiteSpace(username.Text) || string.IsNullOrEmpty(password.Text))
            {
                summary.Text = "Vyplňte uživatelské jméno a heslo.";
                if (string.IsNullOrWhiteSpace(username.Text)) username.Focus(); else password.Focus();
                return;
            }
            var selected = (WsdpOperation)operation.SelectedItem;
            string login = username.Text.Trim();
            string secret = password.Text;
            responseText.Clear();
            save.Enabled = false;
            summary.Text = "Probíhá volání " + selected.Action + "…";
            summary.ForeColor = SystemColors.ControlText;
            status.Text = "Odesílám HTTPS požadavek…";
            SetBusy(true);
            var watch = Stopwatch.StartNew();
            activeCall = new CancellationTokenSource();
            try
            {
                var result = await client.CallAsync(SelectedEnvironment, selected, login, secret,
                    TimeSpan.FromSeconds((double)timeout.Value), activeCall.Token);
                if (IsDisposed || Disposing) return;
                summary.Text = result.Summary;
                summary.ForeColor = result.Success
                    ? (result.HasWarnings ? Color.DarkGoldenrod : Color.DarkGreen) : Color.DarkRed;
                responseText.Text = result.ResponseXml;
                save.Enabled = !string.IsNullOrWhiteSpace(result.ResponseXml);
                tabs.SelectedIndex = 0;
                status.Text = string.Format("HTTP {0} • {1:N2} s • {2}", result.HttpStatus, watch.Elapsed.TotalSeconds,
                    result.Success ? "Dokončeno" : "Chyba služby");
            }
            catch (OperationCanceledException)
            {
                if (!IsDisposed && !Disposing)
                    ShowFailure(activeCall.IsCancellationRequested ? "Volání bylo zrušeno." : "Vypršel časový limit volání.");
            }
            catch (HttpRequestException)
            {
                if (!IsDisposed && !Disposing)
                    ShowFailure("Připojení selhalo. Ověřte internet, DNS, proxy a důvěryhodnost TLS certifikátu služby.");
            }
            catch (InvalidDataException error)
            {
                if (!IsDisposed && !Disposing) ShowFailure(error.Message);
            }
            catch (ArgumentException)
            {
                if (!IsDisposed && !Disposing) ShowFailure("Vstup není platný. Zkontrolujte jméno, heslo a znaky nepovolené v XML.");
            }
            catch (Exception)
            {
                // Do not put exception dumps or request bodies containing credentials into diagnostics.
                if (!IsDisposed && !Disposing) ShowFailure("Volání se nepodařilo dokončit. Zkontrolujte připojení a opakujte test.");
            }
            finally
            {
                secret = null;
                activeCall.Dispose();
                activeCall = null;
                if (!IsDisposed && !Disposing) SetBusy(false);
            }
        }

        private void ShowFailure(string message)
        {
            summary.Text = message;
            summary.ForeColor = Color.DarkRed;
            status.Text = "Volání nebylo dokončeno.";
        }

        private void SaveResponse(object sender, EventArgs args)
        {
            using (var dialog = new SaveFileDialog { Filter = "XML soubor (*.xml)|*.xml", FileName = "wsdp-odpoved.xml" })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                try
                {
                    File.WriteAllText(dialog.FileName, responseText.Text, new UTF8Encoding(false));
                    status.Text = "Odpověď uložena.";
                }
                catch (IOException)
                {
                    MessageBox.Show(this, "Soubor nelze uložit. Zkontrolujte cestu a oprávnění.", "Uložení odpovědi", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                catch (UnauthorizedAccessException)
                {
                    MessageBox.Show(this, "Soubor nelze uložit. Zkontrolujte cestu a oprávnění.", "Uložení odpovědi", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) client.Dispose();
            base.Dispose(disposing);
        }
    }
}

/**
 * DevForge Studio -> Render Free -> Google Apps Script -> Gmail
 *
 * IMPORTANTE:
 * 1) Crie um projeto NOVO em https://script.google.com com a conta
 *    loigi2704@gmail.com (não reutilize o projeto do BBank).
 * 2) Nas propriedades do script, defina DEVFORGE_MAIL_TOKEN com o MESMO
 *    token aleatório (32+ caracteres) que estará nas variáveis do Render.
 * 3) Implante como App da Web, Executar como: Eu, Acesso: Qualquer pessoa.
 *    O token é validado a CADA solicitação. Não divulgue URL/token.
 * 4) O remetente real será a conta Google que implantar o script.
 */

const DEVFORGE_SENDER = 'loigi2704@gmail.com';
const DEVFORGE_ADMINS = Object.freeze([
  'loigi2704@gmail.com',
  'luigi.benicio@gmail.com'
]);

function reply_(result) {
  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function isEmail_(value) {
  return /^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$/.test(value);
}

function doGet() {
  // Apenas teste de vida: não envia e-mail via GET.
  return reply_({ ok: true, service: 'DevForge Mail Gateway', status: 'ready' });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents || e.postData.contents.length > 40000) {
      return reply_({ ok: false, error: 'payload_invalido' });
    }
    let input;
    try {
      input = JSON.parse(e.postData.contents);
    } catch (err) {
      return reply_({ ok: false, error: 'json_invalido' });
    }

    // A URL /exec é acessível publicamente; o token impede envios não autorizados.
    const expectedToken = String(PropertiesService.getScriptProperties()
      .getProperty('DEVFORGE_MAIL_TOKEN') || '');
    const providedToken = String(input.token || '');
    if (expectedToken.length < 32 || providedToken !== expectedToken) {
      return reply_({ ok: false, error: 'nao_autorizado' });
    }

    const executingAccount = String(Session.getEffectiveUser().getEmail() || '')
      .trim().toLowerCase();
    if (executingAccount && executingAccount !== DEVFORGE_SENDER) {
      return reply_({ ok: false, error: 'conta_remetente_incorreta' });
    }

    const to = String(input.to || '').trim().toLowerCase();
    const kind = String(input.kind || 'admin').trim();
    const subject = String(input.subject || '').replace(/[\r\n]+/g, ' ').trim();
    const body = String(input.body || '').trim();

    if (!isEmail_(to) || !subject || !body || subject.length > 240 || body.length > 25000) {
      return reply_({ ok: false, error: 'campos_invalidos' });
    }
    if (kind !== 'admin' && kind !== 'client') {
      return reply_({ ok: false, error: 'tipo_invalido' });
    }
    if (kind === 'admin' && DEVFORGE_ADMINS.indexOf(to) === -1) {
      return reply_({ ok: false, error: 'destinatario_administrativo_bloqueado' });
    }

    // Avisos administrativos: os 2 e-mails fixos acima.
    // Respostas/entregas: o e-mail do cliente que fez o pedido, verificado
    // e gerenciado no servidor Flask (não no navegador).
    if (MailApp.getRemainingDailyQuota() < 1) {
      return reply_({ ok: false, error: 'cota_diaria_esgotada' });
    }

    MailApp.sendEmail({
      to: to,
      subject: subject,
      body: body,
      name: 'DevForge Studio',
      replyTo: DEVFORGE_SENDER
    });
    return reply_({ ok: true });
  } catch (error) {
    console.error('Falha no gateway: ' + String(error));
    return reply_({ ok: false, error: 'falha_envio' });
  }
}

// Execute manualmente no editor para conceder permissão de envio ao projeto.
function autorizarEmail() {
  Logger.log('Destinatários disponíveis hoje: ' + MailApp.getRemainingDailyQuota());
}

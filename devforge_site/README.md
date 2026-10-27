# DevForge Studio — Render Free + Google Apps Script

Portal em **Python/Flask** com pedidos de software, login de clientes, painel de administrador, chat por projeto, arquivos privados e entregas. **Esta edição foi preparada para Render Free** sem SMTP e sem depender do disco temporário do Render.

## Arquitetura

- **Render Free:** hospeda o site Flask e Gunicorn (via `render.yaml`).
- **Google Apps Script (Gmail):** envia e-mails por HTTPS, sem portas SMTP. Quando o cliente cria um pedido ou escreve no chat, **loigi2704@gmail.com** e **luigi.benicio@gmail.com** recebem notificações com o conteúdo das mensagens. Respostas/entregas/status do administrador são notificadas ao e-mail cadastrado do cliente.
- **Supabase PostgreSQL (Free):** persiste usuários, pedidos, chat e índices dos anexos.
- **Supabase Storage (Free):** persiste os arquivos anexados, num **bucket privado**. Downloads passam pelo Flask, que verifica se o usuário é o dono do pedido ou o administrador.
- **Desenvolvimento local:** sem `DATABASE_URL` e sem `SUPABASE_URL`, continua funcionando com SQLite + arquivos locais. Configure um provedor de e-mail apropriado ou o Apps Script também no ambiente local.

> **ATENÇÃO:** o projeto Apps Script não está instalado automaticamente na sua conta Google; você precisa colar o arquivo `apps_script/Code.gs`, conceder permissão e publicar uma implantação `/exec`. Não é seguro colocar tokens ou senhas dentro do código disponibilizado para download.

## 1. Criar o Apps Script usando a conta remetente

1. Entre em **https://script.google.com/** com **loigi2704@gmail.com**.
2. Clique **Novo projeto** e nomeie **DevForge Mail**. Mantenha-o separado do BBank.
3. Abra `apps_script/Code.gs` no ZIP, copie todo o conteúdo e substitua o código do editor Apps Script; salve.
4. Gere um token forte, no PowerShell:

   ```powershell
   py -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

5. No editor Apps Script, abra **Configurações do projeto** (engrenagem), **Propriedades do script**, **Adicionar propriedade**:
   - **Propriedade:** `DEVFORGE_MAIL_TOKEN`
   - **Valor:** cole o token gerado no passo 4.
6. Em **Implantar > Nova implantação > Tipo: Aplicativo da Web**, escolha:
   - **Executar como:** Eu (sua conta `loigi2704@gmail.com`).
   - **Quem pode acessar:** Qualquer pessoa (o script autentica cada requisição com o token). Essa opção deve estar disponível na conta usada.
7. Se o Google ainda não pediu autorização, selecione `autorizarEmail` no editor e clique **Executar** para conceder acesso ao MailApp. Em seguida, clique **Implantar**, aceite as permissões pedidas pelo Google e copie a **URL que termina em `/exec`**.
8. Acesse a URL `/exec` no navegador: deve responder com JSON contendo `"ok":true`, `"status":"ready"`. Isso apenas verifica que o script está público; **não confirma o envio de e-mails**.

**Remetente real:** quem envia é a conta que **implanta o Apps Script** (`loigi2704@gmail.com`), não uma variável de texto. O campo `replyTo` também usa esse endereço. Caso altere o script, faça **Implantar > Gerenciar implantações > Editar > Nova versão**.

**Destinatários:** notificações administrativas só podem ir para `loigi2704@gmail.com` e `luigi.benicio@gmail.com`. E-mails de resposta/entrega podem ir aos clientes cadastrados porque o servidor Flask fornece o endereço e um token secreto. Não disponibilize o token ou a chave do banco na interface ou no GitHub.

## 2. Criar banco e armazenamento persistentes (Supabase Free)

1. Crie um projeto em **https://supabase.com/** no plano **Free**.
2. Em **Connect**, copie o **Session Pooler** (preferencialmente o endereço IPv4) com a senha do banco. Formato de exemplo:

   ```text
   postgresql://postgres.PROJETO:SENHA@aws-0-REGIAO.pooler.supabase.com:5432/postgres
   ```

   Essa URL vai em `DATABASE_URL` nas variáveis do Render. Use os valores da **sua própria conta**, não o exemplo. Se a senha possuir caracteres especiais, codifique-os na URL.
3. No Supabase, abra **Storage > New bucket**. Nome: `devforge-private`. **Public bucket: desligado**. *Não crie políticas públicas de leitura/escrita*.
4. Em **Project Settings / Data API** (a localização pode variar), copie a URL do projeto: `https://SEU-PROJETO.supabase.co`.
5. Em **API Keys**, copie a chave secreta de servidor, preferencialmente `sb_secret_...` (ou a chave `service_role` legada). Coloque no Render como `SUPABASE_SERVICE_ROLE_KEY`. **Nunca use a chave secreta no navegador nem publique em repositórios.**

O backend usa essa chave somente no servidor para escrever e baixar objetos do bucket privado, após verificar as permissões de cliente/admin. Os arquivos têm nomes aleatórios e os metadados ficam no PostgreSQL.

## 3. Publicar no Render Free

1. Envie a pasta `devforge_site` a um **repositório privado GitHub** (sem `.env`, `instance/` nem arquivos de clientes). O arquivo `.gitignore` já inclui essas exclusões.
2. No **Render**, escolha **New > Web Service**, conecte o repositório e marque **Free**.
3. Se detectar `render.yaml`, pode criar por **Blueprint**. Caso configure manualmente, use:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
4. Em **Environment**, configure as seguintes variáveis (a `GOOGLE_MAIL_URL` já está no `render.yaml`; se criar o serviço manualmente, copie a URL de lá):

| Variável | Valor |
|---|---|
| `SECRET_KEY` | Chave aleatória forte; se usar Blueprint, já pode ser gerada |
| `SITE_URL` | `https://SEU-SITE.onrender.com` (sem barra final) |
| `COOKIE_SECURE` | `1` |
| `DATABASE_URL` | URL **Session Pooler** do Supabase (do passo 2) |
| `SUPABASE_URL` | URL do seu projeto Supabase, `https://...supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave secreta Supabase **só no servidor** |
| `SUPABASE_STORAGE_BUCKET` | `devforge-private` |
| `MAIL_PROVIDER` | `appscript` |
| `GOOGLE_MAIL_URL` | Já preenchida no `render.yaml` com a URL do Apps Script informada; confira se está correta |
| `DEVFORGE_MAIL_TOKEN` | Exatamente o token das **Propriedades do script** |
| `ADMIN_EMAILS` | `loigi2704@gmail.com,luigi.benicio@gmail.com` |
| `BOOTSTRAP_ADMIN_NAME` | Nome que quiser no painel |
| `BOOTSTRAP_ADMIN_EMAIL` | Seu endereço usado para fazer login como admin |
| `BOOTSTRAP_ADMIN_PASSWORD` | Senha forte de no mínimo 12 caracteres **temporária** |

5. Publique. Na primeira inicialização, as quatro tabelas são criadas no PostgreSQL, e o primeiro administrador é criado se ainda não existir. Abra `https://SEU-SITE.onrender.com/login` e entre com o e-mail e senha administrativa configurados.
6. Depois de confirmar que você consegue entrar, **remova `BOOTSTRAP_ADMIN_PASSWORD` do ambiente do Render** e salve. Essa variável só é necessária para a primeira inicialização; o usuário já permanece no banco Supabase.
7. Teste na prática: cadastre um cliente de teste, crie um pedido com anexo e envie uma mensagem no chat. Confirme chegada dos e-mails nos **dois endereços**, e abra o arquivo logado na conta certa. Faça um **redeploy** e confirme que o pedido e o arquivo ainda existem.

**Proteção contra perda de dados:** se detectar o ambiente Render sem `DATABASE_URL`, esta versão não inicia, em vez de criar um SQLite temporário e depois apagar pedidos dos clientes.

No Render Free o site **adormece após um período sem visitas**, e a primeira visita pode demorar. O Supabase Free também tem cotas, podendo pausar projetos com pouca atividade. O Apps Script com uma conta Gmail comum tem cotas diárias de destinatários (consulte as cotas do Google); dois destinatários administrativos consomem duas unidades de cota a cada evento.

**Não use Render Postgres Free como armazenamento definitivo:** o banco Free da Render expira depois de 30 dias. O Supabase é uma alternativa de banco externo, ainda que também possua condições e limites do plano Free.

## 4. Rodar no Windows para testar antes do deploy

Execute `iniciar_windows.bat` ou pelo PowerShell:

```powershell
cd .\devforge_site
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite `.env` com uma `SECRET_KEY` aleatória e `DEVFORGE_MAIL_TOKEN` (a `GOOGLE_MAIL_URL` já está preenchida no exemplo). Para rodar **localmente com SQLite**, deixe `DATABASE_URL` e `SUPABASE_URL` comentados (como no exemplo), e configure seu admin:

```powershell
.\.venv\Scripts\python.exe app.py init-admin
.\.venv\Scripts\python.exe app.py test-email
.\.venv\Scripts\python.exe app.py
```

Abra `http://127.0.0.1:5000`. O `test-email` chama o Apps Script via HTTPS e exige confirmação JSON com `"ok":true`. Se houver erro, verifique em **Apps Script > Execuções**, no **Render > Logs**, e confira se a implantação é a versão mais recente.

Para testar com pytest:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
```

## Outras informações importantes

- Existem limites de **5 arquivos por envio**, **20 MB por arquivo** e **50 MB por requisição**.
- O site aceita extensões como ZIP, PDFs, imagens, códigos-fonte e executáveis para **download**, mas **não executa** arquivos enviados ao servidor. É recomendável acrescentar antivírus e monitorar o volume de armazenamento antes de um lançamento público.
- O chat permanece no site e atualiza automaticamente por polling. Notificações enviadas ao Google podem falhar por cota, endpoint ou interrupção de rede; nesse caso, o pedido/mensagem continua salvo no PostgreSQL, mas o e-mail pode não chegar. Confira os logs e a pasta de spam.
- O script tem destinatários administrativos restritos e token, mas a chave e a URL do endpoint devem permanecer privadas; quando suspeitar de exposição, altere o token no Apps Script e no Render.
- O sistema não inclui pagamento, confirmação de e-mail, redefinição de senha ou antivírus integrado. Faça testes e revise segurança/privacidade antes de abrir ao público.
- **O Google Apps Script não substitui o banco:** ele serve para envio de e-mails. Os dados e arquivos precisam de armazenamento externo para sobreviver ao Render Free.

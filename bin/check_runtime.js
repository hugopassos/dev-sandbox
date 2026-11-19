const fs = require('node:fs');
const net = require('node:net');

const mode = process.env.DEV_SANDBOX_CHECK_MODE;

let failed = false;

function report(label, passed) {
  const status = passed ? 'OK' : 'FALHA';

  console.log(`${status}: ${label}`);

  if (!passed) {
    failed = true;
  }
}

function checkWorkspaceRead() {
  try {
    fs.readdirSync('/workspace');
    report('Leitura do workspace', true);
  } catch {
    report('Leitura do workspace', false);
  }
}

function checkWorkspaceWrite() {
  const path = `/workspace/.dev-sandbox-check-${process.pid}-${Date.now()}`;

  let writable = false;

  try {
    fs.writeFileSync(path, 'teste', { flag: 'wx' });
    writable = true;
  } catch {
    writable = false;
  }

  report(
    'Permissão de escrita conforme o modo',
    writable === (mode === 'dev')
  );

  if (writable) {
    try {
      fs.unlinkSync(path);
      report('Remoção do arquivo temporário', true);
    } catch (error) {
      report('Remoção do arquivo temporário', false);
      console.error(`Arquivo pendente: ${path}`);
      console.error(`Motivo: ${error.code || error.message}`);
    }
  }
}

function checkSystemWrite() {
  const path = `/etc/.dev-sandbox-check-${process.pid}`;

  let writable = false;

  try {
    fs.writeFileSync(path, 'teste', { flag: 'wx' });
    writable = true;
  } catch {
    writable = false;
  }

  report('Escrita fora do workspace bloqueada', !writable);

  if (writable) {
    try {
      fs.unlinkSync(path);
    } catch (error) {
      report('Remoção do arquivo de sistema', false);
      console.error(`Arquivo pendente: ${path}`);
    }
  }
}

function checkDirectNetwork() {
  return new Promise(resolve => {
    const socket = net.connect({
      host: '1.1.1.1',
      port: 443,
      timeout: 3000
    });

    let finished = false;

    function finish(connected, reason) {
      if (finished) return;

      finished = true;

      // Timeout significa que a conexão não foi estabelecida,
      // mas não comprova que não exista uma rota externa.
      const blocked = !connected && reason === 'ENETUNREACH';

      report('Conexão direta externa bloqueada', blocked);

      if (!blocked) {
        console.log(`  Resultado da conexão: ${reason}`);
      }

      socket.destroy();
      resolve();
    }

    socket.on('connect', () => finish(true, 'CONNECTED'));
    socket.on('error', error => finish(false, error.code));
    socket.on('timeout', () => finish(false, 'TIMEOUT'));
  });
}

async function main() {
  if (!['readonly', 'dev'].includes(mode)) {
    console.error('Modo inválido:', mode);
    process.exitCode = 1;
    return;
  }

  console.log(`\nVerificando ambiente: ${mode}\n`);

  checkWorkspaceRead();
  checkWorkspaceWrite();
  checkSystemWrite();

  await checkDirectNetwork();

  console.log(
    failed
      ? '\nUma ou mais verificações falharam.'
      : '\nVerificações concluídas com sucesso.'
  );

  if (failed) {
    process.exitCode = 1;
  }
}

main().catch(error => {
  console.error('Erro inesperado:', error);
  process.exitCode = 1;
});

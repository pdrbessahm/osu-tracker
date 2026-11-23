"use strict";

// Painel do osu-tracker: busca /api/painel com os filtros e desenha os graficos com plotly.js.

const estado = { dias: "30", mods: "*", mapa: "todos", dados: null };
const $ = (id) => document.getElementById(id);

// --- formatacao (pt-BR) ---

function num(valor, casas = 1) {
  if (valor === null || valor === undefined) return "-";
  return valor.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
}
const pct = (valor, casas = 1) => (valor === null || valor === undefined ? "-" : num(100 * valor, casas) + "%");
const inteiro = (valor) => (valor === null || valor === undefined ? "-" : valor.toLocaleString("pt-BR"));
const plural = (n, um, varios) => `${inteiro(n)} ${n === 1 ? um : varios}`;

function dataLocal(iso) {
  // O plotly nao converte fuso: passa a hora local deste PC como texto.
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function dataCurta(iso) {
  return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// --- cores e layout comuns (lidos do CSS, entao seguem o modo claro ou escuro do sistema) ---

function cor(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

function layoutBase(extra = {}) {
  const eixo = {
    gridcolor: cor("--grade"), linecolor: cor("--eixo"), zerolinecolor: cor("--eixo"),
    tickfont: { color: cor("--mudo"), size: 12 }, title: { font: { color: cor("--texto-2"), size: 12 } },
    automargin: true,
  };
  return {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: cor("--texto"), size: 13 },
    separators: ",.",
    margin: { l: 56, r: 16, t: 28, b: 40 },
    hoverlabel: { bgcolor: cor("--superficie"), bordercolor: cor("--eixo"), font: { color: cor("--texto") } },
    showlegend: false,
    _eixo: eixo,
    ...extra,
  };
}

function eixo(layout, extra = {}) {
  return { ...layout._eixo, ...extra };
}

function desenhar(id, dados, layout) {
  delete layout._eixo;
  Plotly.react($(id), dados, layout, { displayModeBar: false, responsive: true, locale: "pt-BR" });
}

function mostrarVazio(sufixo, mensagem) {
  const grafico = $("g-" + sufixo);
  const vazio = $("v-" + sufixo);
  if (mensagem) {
    if (grafico) { Plotly.purge(grafico); grafico.hidden = true; }
    vazio.textContent = mensagem;
    vazio.hidden = false;
    return true;
  }
  if (grafico) grafico.hidden = false;
  vazio.hidden = true;
  return false;
}

function tituloPainel(texto, x, y = 1.04) {
  return { text: texto, xref: "paper", yref: "paper", x, y, xanchor: "left", yanchor: "bottom",
           showarrow: false, font: { size: 13, color: cor("--texto-2") } };
}

// --- tabelas ---

function tabela(id, colunas, linhas) {
  const t = $(id);
  t.replaceChildren();
  if (!linhas.length) return;
  const cabeca = t.createTHead().insertRow();
  for (const c of colunas) {
    const th = document.createElement("th");
    th.textContent = c.nome;
    if (c.num) th.className = "num";
    cabeca.appendChild(th);
  }
  const corpo = t.createTBody();
  for (const linha of linhas) {
    const tr = corpo.insertRow();
    for (const c of colunas) {
      const td = tr.insertCell();
      td.textContent = c.valor(linha);
      td.className = [c.num ? "num" : "", c.classe ? c.classe(linha) : ""].join(" ").trim();
    }
  }
}

// --- secoes ---

function resumo(r) {
  const blocos = [
    ["Jogadas", inteiro(r.jogadas), r.jogadas ? `${plural(r.passes, "pass", "passes")}, ${plural(r.jogadas - r.passes, "fail", "fails")}` : ""],
    ["Notas jogadas", inteiro(r.notas), r.jogadas ? plural(r.misses, "miss", "misses") : ""],
    ["Misses por 100 notas", num(r.misses_100), "do trecho jogado"],
    ["Fração jogada", pct(r.fracao_media, 0), "média, até o fail"],
    ["Acc", pct(r.acc, 2), "300/100/50 e misses"],
    ["Acc só das acertadas", pct(r.acc_acertadas, 2), "sem os misses"],
  ];
  $("resumo").replaceChildren(...blocos.map(([nome, valor, detalhe]) => {
    const div = document.createElement("div");
    div.className = "bloco";
    div.innerHTML = '<div class="nome"></div><div class="valor"></div><div class="detalhe"></div>';
    div.children[0].textContent = nome;
    div.children[1].textContent = valor;
    div.children[2].textContent = detalhe;
    return div;
  }));
}

function opcoesMapa(mapas) {
  const select = $("mapa");
  const ids = new Set(mapas.map((m) => m.id));
  if (estado.mapa !== "todos" && !ids.has(estado.mapa)) estado.mapa = "todos";
  const opcoes = [new Option("Todos os mapas (um ponto por dia)", "todos")];
  for (const m of mapas) {
    opcoes.push(new Option(`${m.mapa} · ${m.mods} · ${plural(m.jogadas, "tentativa", "tentativas")}`, m.id));
  }
  select.replaceChildren(...opcoes);
  select.value = estado.mapa;
}

function graficoTempo(d) {
  let pontos, nome, conclusao = "";
  if (estado.mapa === "todos") {
    pontos = d.por_dia.map((p) => ({
      x: p.dia, y: p.misses_100, f: p.fracao_media,
      texto: `${p.dia.split("-").reverse().join("/")}<br>${plural(p.jogadas, "jogada", "jogadas")}, ${plural(p.misses, "miss", "misses")} em ${inteiro(p.notas)} notas`,
    }));
    nome = "dia";
  } else {
    const mapa = d.por_mapa.find((m) => m.id === estado.mapa);
    pontos = mapa.tentativas.map((t, i) => ({
      x: i + 1, y: t.misses_100, f: t.fracao,
      texto: `Tentativa ${i + 1}, ${dataCurta(t.quando)}<br>${plural(t.misses, "miss", "misses")} em ${inteiro(t.notas)} notas${t.passou ? " (pass)" : ""}<br>acc ${pct(t.acc, 2)}`,
    }));
    nome = "tentativa";
    const td = mapa.tendencia;
    const palavras = { caindo: "caindo", subindo: "subindo", "estável": "estáveis", "sem dados": "sem dados" };
    conclusao = `Misses ${palavras[td.sentido]}: ${num(td.antes)} por 100 notas na primeira metade das tentativas, ${num(td.depois)} na segunda.`;
  }
  $("v-tempo").hidden = true;
  if (mostrarVazio("tempo", pontos.length ? "" : "Nenhuma jogada no período e nos mods escolhidos.")) return;

  const l = layoutBase({ margin: { l: 56, r: 16, t: 16, b: 40 } });
  // Dois paineis com o mesmo eixo x (um embaixo do outro), cada um com a propria escala.
  // Todos os mapas: um ponto por dia. Um mapa: uma tentativa depois da outra (a data fica no detalhe).
  const porDia = estado.mapa === "todos";
  const x = porDia ? { type: "date", tickformat: "%d/%m", dtick: 86400000 }
    : { type: "linear", dtick: 1, range: [0.5, pontos.length + 0.5] };
  l.xaxis = eixo(l, { ...x, anchor: "y", matches: "x2", showticklabels: false });
  l.xaxis2 = eixo(l, { ...x, anchor: "y2", title: { text: porDia ? "" : "tentativa", font: { color: cor("--texto-2"), size: 12 } } });
  l.yaxis = eixo(l, { domain: [0.42, 1], rangemode: "tozero", title: { text: "misses por 100 notas", font: { color: cor("--texto-2"), size: 12 } } });
  l.yaxis2 = eixo(l, { domain: [0, 0.3], range: [0, 1.05], tickformat: ".0%", title: { text: "fração jogada", font: { color: cor("--texto-2"), size: 12 } } });
  if (porDia && pontos.length === 1) {
    // Um dia so: o plotly abriria um intervalo enorme; mostra o dia anterior e o seguinte.
    const dia = new Date(pontos[0].x + "T12:00:00Z");
    const vizinho = (n) => new Date(dia.getTime() + n * 86400e3).toISOString().slice(0, 10);
    l.xaxis2.range = [vizinho(-1), vizinho(1)];
  }
  const s1 = cor("--serie-1");
  desenhar("g-tempo", [
    { x: pontos.map((p) => p.x), y: pontos.map((p) => p.y), type: "scatter", mode: "lines+markers",
      line: { color: s1, width: 2 }, marker: { color: s1, size: 9, line: { color: cor("--superficie"), width: 2 } },
      text: pontos.map((p) => p.texto), hovertemplate: "%{text}<br><b>%{y:.1f} misses por 100 notas</b><extra></extra>",
      xaxis: "x", yaxis: "y" },
    { x: pontos.map((p) => p.x), y: pontos.map((p) => p.f), type: "scatter", mode: "markers",
      marker: { color: s1, size: 9, symbol: "square", line: { color: cor("--superficie"), width: 2 } },
      text: pontos.map((p) => p.texto), hovertemplate: `%{text}<br><b>chegou a %{y:.0%} do mapa</b>${nome === "dia" ? " (média)" : ""}<extra></extra>`,
      xaxis: "x2", yaxis: "y2" },
  ], l);
  const c = $("c-tempo");
  if (c) c.textContent = conclusao;
}

function tabelaMapas(mapas) {
  tabela("t-mapas", [
    { nome: "Mapa", valor: (m) => m.mapa, classe: () => "mapa" },
    { nome: "Mods", valor: (m) => m.mods },
    { nome: "★", valor: (m) => num(m.estrelas, 2), num: true },
    { nome: "Tentativas", valor: (m) => inteiro(m.jogadas), num: true },
    { nome: "Misses/100 (1ª metade)", valor: (m) => num(m.tendencia.antes), num: true },
    { nome: "Misses/100 (2ª metade)", valor: (m) => num(m.tendencia.depois), num: true },
    { nome: "Tendência", valor: (m) => m.tendencia.sentido, classe: (m) => (m.tendencia.sentido === "caindo" ? "caindo" : m.tendencia.sentido === "subindo" ? "subindo" : "") },
    { nome: "Mais longe", valor: (m) => pct(m.maior_fracao, 0), num: true },
  ], mapas);
}

function graficoFaixas(faixas) {
  const t = "t-faixas";
  if (mostrarVazio("faixas", faixas.length ? "" : "Nenhuma jogada com estrelas conhecidas no período e nos mods escolhidos.")) {
    tabela(t, [], []);
    return;
  }
  // Rotulo curto (inicio da faixa) para caber nos tres graficos lado a lado; o nome inteiro fica no detalhe.
  const x = faixas.map((f) => num(f.de, 1) + "★");
  const texto = faixas.map((f) => `${f.nome}<br>${plural(f.jogadas, "jogada", "jogadas")} em ${plural(f.mapas, "mapa", "mapas")}`);
  const l = layoutBase({ margin: { l: 48, r: 12, t: 36, b: 48 }, bargap: 0.3 });
  l.grid = { rows: 1, columns: 3, pattern: "independent", xgap: 0.12 };
  const series = [
    ["misses_100", "Misses por 100 notas", (v) => v, ".1f", ""],
    ["fracao_media", "Fração jogada (média)", (v) => v, ".0%", ".0%"],
    ["acc", "Acc", (v) => v, ".2%", ".0%"],
  ];
  const s1 = cor("--serie-1");
  const dados = series.map(([chave, , , fmt], i) => ({
    x, y: faixas.map((f) => f[chave]), type: "bar", marker: { color: s1, cornerradius: 4 },
    xaxis: i ? `x${i + 1}` : "x", yaxis: i ? `y${i + 1}` : "y",
    customdata: texto, hovertemplate: `%{customdata}<br><b>%{y:${fmt}}</b><extra></extra>`,
  }));
  l.annotations = [];
  series.forEach(([, titulo, , , tick], i) => {
    const sufixo = i ? String(i + 1) : "";
    l["xaxis" + sufixo] = eixo(l, { type: "category", tickangle: 0 });
    l["yaxis" + sufixo] = eixo(l, { rangemode: "tozero", tickformat: tick || undefined });
    l.annotations.push({ ...tituloPainel(titulo, 0, 1.02), xref: `x${sufixo} domain`, yref: `y${sufixo} domain` });
  });
  // acc perto de 100% faria barras quase iguais a partir do zero: mostra so a parte de cima.
  const accs = faixas.map((f) => f.acc).filter((v) => v !== null);
  if (accs.length) l.yaxis3.range = [Math.max(0, Math.min(...accs) - 0.1), 1];
  l.yaxis3.rangemode = undefined;
  desenhar("g-faixas", dados, l);

  tabela(t, [
    { nome: "Faixa", valor: (f) => f.nome },
    { nome: "Jogadas", valor: (f) => inteiro(f.jogadas), num: true },
    { nome: "Mapas", valor: (f) => inteiro(f.mapas), num: true },
    { nome: "Passes", valor: (f) => inteiro(f.passes), num: true },
    { nome: "Notas jogadas", valor: (f) => inteiro(f.notas), num: true },
    { nome: "Misses/100 notas", valor: (f) => num(f.misses_100), num: true },
    { nome: "Fração jogada", valor: (f) => pct(f.fracao_media, 0), num: true },
    { nome: "Acc", valor: (f) => pct(f.acc, 2), num: true },
    { nome: "Acc só acertadas", valor: (f) => pct(f.acc_acertadas, 2), num: true },
  ], faixas);
}

function graficoAim(a) {
  const conclusao = $("c-aim");
  if (mostrarVazio("aim", a.aim.jogadas >= 2 && a.speed.jogadas >= 2 ? ""
    : "Ainda poucos dados: é preciso ter jogado mapas diferentes dentro da mesma faixa de estrelas (pelo menos duas jogadas de cada tipo).")) {
    conclusao.textContent = "";
    return;
  }
  const grupos = [["Mapas de aim", a.aim, cor("--serie-1")], ["Mapas de speed", a.speed, cor("--serie-2")]];
  const l = layoutBase({ margin: { l: 48, r: 12, t: 36, b: 40 }, bargap: 0.35 });
  l.grid = { rows: 1, columns: 2, pattern: "independent", xgap: 0.15 };
  const texto = grupos.map(([nome, g]) => `${nome}<br>${plural(g.jogadas, "jogada", "jogadas")} em ${plural(g.mapas, "mapa", "mapas")}`);
  l.xaxis = eixo(l, { type: "category" });
  l.xaxis2 = eixo(l, { type: "category" });
  l.yaxis = eixo(l, { rangemode: "tozero" });
  l.yaxis2 = eixo(l, { rangemode: "tozero", tickformat: ".0%" });
  l.annotations = [
    { ...tituloPainel("Misses por 100 notas", 0, 1.02), xref: "x domain", yref: "y domain" },
    { ...tituloPainel("Fração jogada (média)", 0, 1.02), xref: "x2 domain", yref: "y2 domain" },
  ];
  const barra = (chave, eixoX, eixoY, fmt, rotulo) => ({
    x: grupos.map((g) => g[0]), y: grupos.map((g) => g[1][chave]), type: "bar", xaxis: eixoX, yaxis: eixoY,
    marker: { color: grupos.map((g) => g[2]), cornerradius: 4 },
    text: grupos.map((g) => rotulo(g[1][chave])), textposition: "outside", cliponaxis: false,
    textfont: { color: cor("--texto") }, customdata: texto, hovertemplate: `%{customdata}<br><b>%{y:${fmt}}</b><extra></extra>`,
  });
  desenhar("g-aim", [
    barra("misses_100", "x", "y", ".1f", (v) => num(v)),
    barra("fracao_media", "x2", "y2", ".0%", (v) => pct(v, 0)),
  ], l);

  const ma = a.aim.misses_100, ms = a.speed.misses_100;
  const poucos = Math.min(a.aim.jogadas, a.speed.jogadas) < 10 ? " Ainda são poucas jogadas: trate como indício." : "";
  if (Math.abs(ma - ms) < 0.1 * Math.max(ma, ms)) {
    conclusao.textContent = `Você erra parecido nos dois tipos (${num(ma)} contra ${num(ms)} misses por 100 notas).${poucos}`;
  } else {
    const pior = ma > ms ? "aim" : "speed";
    const vezes = Math.max(ma, ms) / Math.max(Math.min(ma, ms), 0.01);
    conclusao.textContent = `Você erra mais em mapas de ${pior}: ${num(Math.max(ma, ms))} contra ${num(Math.min(ma, ms))} misses por 100 notas (${num(vezes, 1)}x).${poucos}`;
  }
}

function graficoAcc(pontos) {
  if (mostrarVazio("acc", pontos.length ? "" : "Nenhuma jogada no período e nos mods escolhidos.")) return;
  const x = pontos.map((p) => dataLocal(p.quando));
  const texto = pontos.map((p) => `${p.mapa}<br>${p.mods} · ${num(p.estrelas, 2)}★ · ${dataCurta(p.quando)}<br>${plural(p.misses, "miss", "misses")} em ${inteiro(p.notas)} notas`);
  const l = layoutBase({ showlegend: true, legend: { orientation: "h", x: 0, y: 1.12, font: { color: cor("--texto-2") } } });
  l.xaxis = eixo(l, { type: "date", tickformat: "%d/%m %H:%M" });
  l.yaxis = eixo(l, { tickformat: ".0%", range: [Math.max(0, Math.min(...pontos.map((p) => p.acc)) - 0.05), 1.005] });
  const serie = (chave, nome, c, simbolo) => ({
    x, y: pontos.map((p) => p[chave]), name: nome, type: "scatter", mode: "markers", text: texto,
    marker: { color: c, size: 9, symbol: simbolo, line: { color: cor("--superficie"), width: 1.5 } },
    hovertemplate: `%{text}<br><b>${nome}: %{y:.2%}</b><extra></extra>`,
  });
  desenhar("g-acc", [
    serie("acc_acertadas", "Acc só das acertadas", cor("--serie-1"), "circle"),
    serie("acc", "Acc com os misses", cor("--serie-2"), "diamond"),
  ], l);
}

function sugestoes(lista, regras) {
  $("regra-sugestoes").textContent =
    `Regra: para cada mapa (com os mesmos mods) em que você ainda não tem FC, o painel multiplica a sua taxa de misses ` +
    `nele (somando as tentativas) pelo número de notas do mapa inteiro. Isso dá quantos misses você faria hoje se ` +
    `jogasse o mapa até o fim. A lista mostra os ${regras.limite_sugestoes} mapas com a menor estimativa: são os que ` +
    `estão mais perto de um FC ou de poucos misses. Só entram mapas com pelo menos ${regras.amostra_minima_sugestao} ` +
    `notas jogadas, para uma tentativa curta de sorte não enganar.`;
  if (mostrarVazio("sugestoes", lista.length ? "" : `Nenhum mapa ainda tem ${regras.amostra_minima_sugestao} notas jogadas no período e nos mods escolhidos.`)) {
    tabela("t-sugestoes", [], []);
    return;
  }
  tabela("t-sugestoes", [
    { nome: "Mapa", valor: (m) => m.mapa, classe: () => "mapa" },
    { nome: "Mods", valor: (m) => m.mods },
    { nome: "★", valor: (m) => num(m.estrelas, 2), num: true },
    { nome: "Misses estimados no mapa todo", valor: (m) => num(m.misses_estimados), num: true },
    { nome: "Misses/100 notas", valor: (m) => num(m.misses_100), num: true },
    { nome: "Tentativas", valor: (m) => inteiro(m.jogadas), num: true },
    { nome: "Mais longe", valor: (m) => pct(m.maior_fracao, 0), num: true },
    { nome: "Acc", valor: (m) => pct(m.acc, 2), num: true },
  ], lista);
}

// --- carga ---

function opcoesMods(disponiveis) {
  const select = $("mods");
  const opcoes = [new Option("Todos", "*")];
  for (const m of disponiveis) opcoes.push(new Option(`${m.nome} (${inteiro(m.jogadas)})`, m.valor));
  if (estado.mods !== "*" && !disponiveis.some((m) => m.valor === estado.mods)) estado.mods = "*";
  select.replaceChildren(...opcoes);
  select.value = estado.mods;
}

function desenharTudo() {
  const d = estado.dados;
  if (!d || !d.resumo) return;
  resumo(d.resumo);
  opcoesMapa(d.por_mapa);
  graficoTempo(d);
  tabelaMapas(d.por_mapa);
  graficoFaixas(d.por_faixa);
  graficoAim(d.aim_speed);
  graficoAcc(d.acc);
  sugestoes(d.sugestoes, d.regras);
}

async function carregar() {
  const params = new URLSearchParams();
  if (estado.dias) params.set("dias", estado.dias);
  if (estado.mods !== "*") params.set("mods", estado.mods);
  for (const b of $("periodo").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.dias === estado.dias));
  let d;
  try {
    const resposta = await fetch("/api/painel?" + params);
    if (!resposta.ok) throw new Error(`o servidor respondeu ${resposta.status}`);
    d = await resposta.json();
  } catch (e) {
    d = { aviso: `Não consegui falar com o osu-tracker (${e.message}). Ele ainda está aberto?` };
  }
  estado.dados = d;
  $("jogador").textContent = d.jogador ? `Jogador: ${d.jogador} · osu! standard` : "osu! standard";
  $("aviso").textContent = d.aviso || "";
  $("aviso").hidden = !d.aviso;
  opcoesMods(d.mods_disponiveis || []);
  $("painel-analise").classList.toggle("sem-dados", !d.resumo);
  for (const s of document.querySelectorAll("#painel-analise > section")) s.hidden = !d.resumo;
  if (d.resumo && !d.resumo.jogadas && !d.aviso) {
    $("aviso").textContent = estado.dias || estado.mods !== "*"
      ? "Nenhuma jogada com esses filtros. Experimente um período maior ou Todos os mods."
      : "Ainda não há jogadas no banco. Elas aparecem aqui depois da primeira coleta com o osu! aberto.";
    $("aviso").hidden = false;
  }
  desenharTudo();
  document.body.dataset.carregado = "1";
}

$("periodo").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  estado.dias = b.dataset.dias;
  carregar();
});
$("mods").addEventListener("change", (e) => { estado.mods = e.target.value; carregar(); });
$("mapa").addEventListener("change", (e) => { estado.mapa = e.target.value; graficoTempo(estado.dados); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  desenharTudo();
  if (window.redesenharProgresso) window.redesenharProgresso();
});

// --- abas ---

function mostrarAba(aba) {
  for (const b of document.querySelectorAll(".abas button")) b.setAttribute("aria-selected", String(b.dataset.aba === aba));
  $("painel-analise").hidden = aba !== "analise";
  $("painel-progresso").hidden = aba !== "progresso";
  $("filtros").hidden = aba !== "analise"; // periodo e mods valem so para a Analise
  if (aba === "progresso" && window.carregarProgresso) window.carregarProgresso();
  if (aba === "analise") desenharTudo(); // o plotly precisa medir o grafico visivel
  if (location.hash !== "#" + aba) history.replaceState(null, "", "#" + aba);
}
document.querySelector(".abas").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) mostrarAba(b.dataset.aba);
});

carregar().then(() => mostrarAba(location.hash === "#progresso" ? "progresso" : "analise"));

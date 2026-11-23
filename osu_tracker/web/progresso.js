"use strict";

// Aba Progresso: busca /api/progresso e desenha perfil, estrelas, aim/speed/acc, skillsets e o classificador.
// Usa os auxiliares de painel.js ($, num, pct, inteiro, plural, cor, layoutBase, eixo, desenhar, tabela).

const prog = { dados: null, carregando: false };
const TIPOS = ["stream", "jump", "tech", "alternate"];
const NOME_TIPO = { stream: "Stream", jump: "Jump", tech: "Tech", alternate: "Alternate" };

const diaCurto = (iso) => iso.split("-").reverse().slice(0, 2).join("/");
const semanaTexto = (iso) => `semana de ${diaCurto(iso)}`;

function vazioP(sufixo, mensagem) {
  const g = $("g-" + sufixo), v = $("v-" + sufixo);
  if (mensagem) {
    Plotly.purge(g); g.hidden = true; v.textContent = mensagem; v.hidden = false; return true;
  }
  g.hidden = false; v.hidden = true; return false;
}

// Bloco com valor atual e a variacao em 7 e 30 dias. melhor: +1 se subir e bom, -1 se descer e bom.
function blocoVariacao(nome, valor, variacoes, fmt, melhor = 1, detalhe = "") {
  const div = document.createElement("div");
  div.className = "bloco";
  const linhas = variacoes.map(([rotulo, v]) => {
    if (v === null || v === undefined) return `<span class="var">${rotulo}: sem dado</span>`;
    const classe = v === 0 ? "" : (v * melhor > 0 ? "caindo" : "subindo"); // reaproveita verde e vermelho
    const seta = v > 0 ? "▲" : v < 0 ? "▼" : "=";
    const texto = (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v));
    return `<span class="var ${classe}">${rotulo}: ${seta} ${texto}</span>`;
  });
  div.innerHTML = `<div class="nome"></div><div class="valor"></div><div class="detalhe"></div><div class="variacoes">${linhas.join("")}</div>`;
  div.children[0].textContent = nome;
  div.children[1].textContent = valor;
  div.children[2].textContent = detalhe;
  return div;
}

// --- perfil ---

function perfil(p) {
  const a = p.atual;
  const v7 = p.variacao["7"] || {}, v30 = p.variacao["30"] || {};
  const vars = (campo) => [["7 dias", v7[campo]], ["30 dias", v30[campo]]];
  if (!a) {
    $("p-perfil").replaceChildren();
  } else {
    $("p-perfil").replaceChildren(
      blocoVariacao("pp", num(a.pp, 0), vars("pp"), (x) => num(x, 0)),
      blocoVariacao("Rank global", a.rank_global ? "#" + inteiro(a.rank_global) : "-", vars("rank_global"), (x) => inteiro(Math.round(x)), -1),
      blocoVariacao("Rank no país", a.rank_pais ? "#" + inteiro(a.rank_pais) : "-", vars("rank_pais"), (x) => inteiro(Math.round(x)), -1),
      blocoVariacao("Acc do perfil", pct(a.acuracia, 2), vars("acuracia"), (x) => num(100 * x, 2) + " p.p."),
      blocoVariacao("Play count", inteiro(a.play_count), vars("play_count"), (x) => inteiro(Math.round(x))),
      blocoVariacao("Ranked score", inteiro(a.ranked_score), vars("ranked_score"), (x) => inteiro(Math.round(x))),
      blocoVariacao("Nível", num(a.nivel, 2), vars("nivel"), (x) => num(x, 2)),
    );
  }
  const s1 = cor("--serie-1");
  const linha = (x, y, texto, fmtHover) => ({
    x, y, type: "scatter", mode: x.length > 1 ? "lines" : "markers", text: texto,
    line: { color: s1, width: 2 }, marker: { color: s1, size: 9, line: { color: cor("--superficie"), width: 2 } },
    hovertemplate: `%{text}<br><b>${fmtHover}</b><extra></extra>`,
  });
  if (!vazioP("rank", p.rank.length ? "" : "Sem rank ainda: aparece depois da primeira coleta do dia.")) {
    const l = layoutBase({ margin: { l: 64, r: 16, t: 12, b: 36 } });
    l.xaxis = eixo(l, { type: "date", tickformat: "%d/%m" });
    // Rank menor e melhor: eixo invertido, para "subir" no grafico ser melhorar.
    l.yaxis = eixo(l, { autorange: "reversed", tickformat: ",d", title: { text: "rank (menor é melhor)", font: { color: cor("--texto-2"), size: 12 } } });
    desenhar("g-rank", [linha(p.rank.map((r) => r.dia), p.rank.map((r) => r.rank_global),
      p.rank.map((r) => diaCurto(r.dia)), "#%{y:,d}")], l);
  }
  const pp = p.fotos.filter((f) => f.pp !== null);
  if (!vazioP("pp", pp.length ? "" : "Sem foto do perfil ainda: aparece depois da primeira coleta do dia.")) {
    const l = layoutBase({ margin: { l: 56, r: 16, t: 12, b: 36 } });
    l.xaxis = eixo(l, { type: "date", tickformat: "%d/%m" });
    l.yaxis = eixo(l, { tickformat: ",.0f" });
    if (pp.length === 1) {
      const d = new Date(pp[0].dia + "T12:00:00Z");
      const viz = (n) => new Date(d.getTime() + n * 86400e3).toISOString().slice(0, 10);
      l.xaxis.range = [viz(-3), viz(3)];
    }
    desenhar("g-pp", [linha(pp.map((f) => f.dia), pp.map((f) => f.pp), pp.map((f) => diaCurto(f.dia)), "%{y:,.1f} pp")], l);
  }
}

// --- semanas ---

const valorDe = (t) => (t ? t.valor : null);
const detalheDe = (t, unidade) => (t ? `${num(t.valor, 2)}${unidade} · ${t.mapa} · ${t.mods}` : "nenhum");

function variacaoJanela(janelas, chave, extrair = valorDe) {
  return ["7", "30"].map((n) => {
    const j = janelas[n];
    const a = extrair(j.recente[chave]), b = extrair(j.anterior[chave]);
    return [`${n} dias`, a === null || b === null || a === undefined || b === undefined ? null : a - b];
  });
}

function graficoLinhas(id, semanas, series, opcoes = {}) {
  const l = layoutBase({ margin: { l: 48, r: 12, t: 12, b: 36 }, showlegend: series.length > 1,
    legend: { orientation: "h", x: 0, y: 1.18, font: { color: cor("--texto-2") } } });
  // Um tick por semana, na segunda-feira de cada ponto.
  l.xaxis = eixo(l, { type: "date", tickformat: "%d/%m", dtick: 7 * 86400000, tick0: semanas.length ? semanas[0].semana : undefined, ...(semanas.length ? { range: semanas.length === 1
    ? [diaMais(semanas[0].semana, -7), diaMais(semanas[0].semana, 7)]
    : [diaMais(semanas[0].semana, -2), diaMais(semanas[semanas.length - 1].semana, 2)] } : {}) });
  l.yaxis = eixo(l, opcoes.yaxis || {});
  const dados = series.map((s) => {
    const pontos = semanas.map((w) => ({ x: w.semana, t: s.ponto(w) })).filter((p) => p.t && p.t.y !== null && p.t.y !== undefined);
    return {
      x: pontos.map((p) => p.x), y: pontos.map((p) => p.t.y), name: s.nome, type: "scatter",
      mode: pontos.length > 1 ? "lines+markers" : "markers", text: pontos.map((p) => `${semanaTexto(p.x)}<br>${p.t.texto}`),
      line: { color: s.cor, width: 2, dash: s.tracejado ? "dot" : "solid" },
      marker: { color: s.cor, size: 9, line: { color: cor("--superficie"), width: 2 } },
      hovertemplate: `%{text}<extra>${s.nome}</extra>`,
    };
  });
  desenhar(id, dados, l);
}

function diaMais(iso, n) {
  return new Date(new Date(iso + "T12:00:00Z").getTime() + n * 86400e3).toISOString().slice(0, 10);
}

const pontoTeto = (chave, unidade) => (w) => (w[chave] ? { y: w[chave].valor, texto: `${num(w[chave].valor, 2)}${unidade} · ${w[chave].mapa} · ${w[chave].mods}` } : null);

function estrelas(semanas, janelas) {
  const r7 = janelas["7"].recente;
  $("p-estrelas").replaceChildren(
    blocoVariacao("Maior estrela passada (7 dias)", r7.estrelas_pass ? num(r7.estrelas_pass.valor, 2) + "★" : "-", variacaoJanela(janelas, "estrelas_pass"), (x) => num(x, 2) + "★", 1, r7.estrelas_pass ? r7.estrelas_pass.mapa : "nenhum pass"),
    blocoVariacao("Maior estrela com FC (7 dias)", r7.estrelas_fc ? num(r7.estrelas_fc.valor, 2) + "★" : "-", variacaoJanela(janelas, "estrelas_fc"), (x) => num(x, 2) + "★", 1, r7.estrelas_fc ? r7.estrelas_fc.mapa : "nenhum FC"),
    blocoVariacao("Maior estrela tentada (7 dias)", r7.estrelas_tentada ? num(r7.estrelas_tentada.valor, 2) + "★" : "-", variacaoJanela(janelas, "estrelas_tentada"), (x) => num(x, 2) + "★", 1, r7.estrelas_tentada ? r7.estrelas_tentada.mapa : ""),
  );
  if (!vazioP("estrelas", semanas.length ? "" : "Nenhuma jogada ainda.")) {
    graficoLinhas("g-estrelas", semanas, [
      { nome: "Passou", cor: cor("--serie-1"), ponto: pontoTeto("estrelas_pass", "★") },
      { nome: "FC", cor: cor("--serie-2"), ponto: pontoTeto("estrelas_fc", "★") },
      { nome: "Tentou (a mais difícil)", cor: cor("--mudo"), tracejado: true, ponto: pontoTeto("estrelas_tentada", "★") },
    ], { yaxis: { title: { text: "estrelas", font: { color: cor("--texto-2"), size: 12 } } } });
  }
  tabela("t-estrelas", [
    { nome: "Semana", valor: (w) => diaCurto(w.semana) },
    { nome: "Jogadas", valor: (w) => inteiro(w.jogadas), num: true },
    { nome: "Passes", valor: (w) => inteiro(w.passes), num: true },
    { nome: "FCs", valor: (w) => inteiro(w.fcs), num: true },
    { nome: "Maior ★ passada", valor: (w) => detalheDe(w.estrelas_pass, "★"), classe: () => "mapa" },
    { nome: "Maior ★ com FC", valor: (w) => detalheDe(w.estrelas_fc, "★"), classe: () => "mapa" },
    { nome: "Maior ★ tentada", valor: (w) => detalheDe(w.estrelas_tentada, "★"), classe: () => "mapa" },
  ], [...semanas].reverse());
}

function habilidades(semanas, janelas) {
  const r7 = janelas["7"].recente;
  const v = (t) => (t ? num(t.valor, 2) : "-");
  $("p-habilidades").replaceChildren(
    blocoVariacao("Maior aim passado (7 dias)", v(r7.aim_pass), variacaoJanela(janelas, "aim_pass"), (x) => num(x, 2), 1, r7.aim_pass ? r7.aim_pass.mapa : "nenhum pass"),
    blocoVariacao("Maior speed passado (7 dias)", v(r7.speed_pass), variacaoJanela(janelas, "speed_pass"), (x) => num(x, 2), 1, r7.speed_pass ? r7.speed_pass.mapa : "nenhum pass"),
    blocoVariacao("Acc (7 dias)", pct(r7.acc, 2), variacaoJanela(janelas, "acc", (x) => x), (x) => num(100 * x, 2) + " p.p.", 1,
      r7.od_medio !== null ? `OD médio ${num(r7.od_medio, 1)}` : ""),
  );
  const nenhum = "Nenhum pass ainda: aparece quando você passar um mapa.";
  const temPass = semanas.some((w) => w.aim_pass || w.speed_pass);
  const series = (chave) => [
    { nome: "Passou", cor: cor("--serie-1"), ponto: pontoTeto(chave + "_pass", "") },
    { nome: "FC", cor: cor("--serie-2"), ponto: pontoTeto(chave + "_fc", "") },
  ];
  if (!vazioP("aim-semana", temPass ? "" : nenhum)) graficoLinhas("g-aim-semana", semanas, series("aim"));
  if (!vazioP("speed-semana", temPass ? "" : nenhum)) graficoLinhas("g-speed-semana", semanas, series("speed"));
  const comAcc = semanas.filter((w) => w.acc !== null);
  if (!vazioP("acc-semana", comAcc.length ? "" : "Nenhuma jogada ainda.")) {
    graficoLinhas("g-acc-semana", comAcc, [{
      nome: "Acc", cor: cor("--serie-1"),
      ponto: (w) => ({ y: w.acc, texto: `acc ${pct(w.acc, 2)} · só acertadas ${pct(w.acc_acertadas, 2)}<br>OD médio ${num(w.od_medio, 1)} · ${plural(w.jogadas, "jogada", "jogadas")}` }),
    }], { yaxis: { tickformat: ".1%" } });
  }
}

// --- skillsets ---

function skillsets(s) {
  $("p-sem-features").textContent = s.sem_features
    ? `${plural(s.sem_features, "mapa jogado ainda não foi lido", "mapas jogados ainda não foram lidos")}: o .osu é baixado aos poucos nas próximas coletas.`
    : "";
  const caixa = $("p-skillsets");
  caixa.replaceChildren();
  for (const t of s.tipos) {
    const div = document.createElement("div");
    div.className = "skillset";
    div.innerHTML = `<h3></h3><p class="detalhe"></p><div class="grafico baixo" id="g-skill-${t.tipo}"></div><p class="vazio" id="v-skill-${t.tipo}" hidden></p><table></table>`;
    div.querySelector("h3").textContent = t.nome;
    div.querySelector(".detalhe").textContent = `${plural(t.mapas, "mapa", "mapas")} · ${plural(t.total.jogadas, "jogada", "jogadas")} · ${plural(t.total.fcs, "FC", "FCs")}`;
    caixa.appendChild(div);
    const linhas = [
      ["Acc", "acc", (x) => pct(x, 2), (x) => num(100 * x, 2) + " p.p.", 1, (x) => x],
      ["Misses/100 notas", "misses_100", (x) => num(x), (x) => num(x), -1, (x) => x],
      ["Maior pp", "maior_pp", (x) => (x ? num(x.valor, 0) : "-"), (x) => num(x, 0), 1, valorDe],
      ["FCs", "fcs", (x) => inteiro(x), (x) => inteiro(x), 1, (x) => x],
    ];
    const tab = div.querySelector("table");
    const cab = tab.createTHead().insertRow();
    for (const [nome, cls] of [["", ""], ["7 dias", "num"], ["vs 7 antes", "num"], ["30 dias", "num"], ["vs 30 antes", "num"]]) {
      const th = document.createElement("th"); th.textContent = nome; th.className = cls; cab.appendChild(th);
    }
    const corpo = tab.createTBody();
    for (const [nome, chave, fmt, fmtVar, melhor, extrair] of linhas) {
      const tr = corpo.insertRow();
      tr.insertCell().textContent = nome;
      for (const n of ["7", "30"]) {
        const j = t.janelas[n];
        const c = tr.insertCell(); c.className = "num"; c.textContent = fmt(j.recente[chave]);
        const a = extrair(j.recente[chave]), b = extrair(j.anterior[chave]);
        const d = tr.insertCell(); d.className = "num";
        if (a === null || b === null || a === undefined || b === undefined || (chave !== "fcs" && (!j.recente.jogadas || !j.anterior.jogadas))) {
          d.textContent = "-";
        } else {
          const diff = a - b;
          d.textContent = (diff > 0 ? "▲ +" : diff < 0 ? "▼ −" : "= ") + fmtVar(Math.abs(diff));
          if (diff) d.classList.add(diff * melhor > 0 ? "caindo" : "subindo");
        }
      }
    }
  }
  // Graficos so depois de todos os cartoes na grade: antes disso o primeiro mede a largura da linha inteira.
  for (const t of s.tipos) {
    const comAcc = t.semanas.filter((w) => w.acc !== null);
    if (!vazioP(`skill-${t.tipo}`, comAcc.length ? "" : "Nenhuma jogada em mapas deste tipo ainda.")) {
      graficoLinhas(`g-skill-${t.tipo}`, comAcc, [{
        nome: "Acc", cor: cor("--serie-1"),
        ponto: (w) => ({ y: w.acc, texto: `acc ${pct(w.acc, 2)} · ${num(w.misses_100)} misses/100<br>${plural(w.jogadas, "jogada", "jogadas")} · ${plural(w.fcs, "FC", "FCs")}${w.maior_pp ? ` · maior pp ${num(w.maior_pp.valor, 0)}` : ""}` }),
      }], { yaxis: { tickformat: ".2%", nticks: 5 } }); // acc perto entre as semanas: 2 casas para nao repetir rotulo
    }
  }
}

// --- classificador e marcacao ---

function classificador(s) {
  const r = s.regras;
  $("p-regra-classificador").textContent =
    `Cada tipo começa por uma regra sobre o .osu. Quando um tipo tem pelo menos ${r.minimo_por_classe} mapas marcados como “é” ` +
    `e ${r.minimo_por_classe} como “não é”, um modelo (regressão logística sobre as mesmas medidas do .osu) treinado com as suas ` +
    `marcações substitui a regra daquele tipo, e é treinado de novo a cada marcação. A precisão estimada é a média do acerto nos ` +
    `mapas que são e nos que não são do tipo, numa validação cruzada; abaixo de ${pct(r.precisao_baixa, 0)} o painel avisa.`;
  tabela("t-classificador", [
    { nome: "Tipo", valor: (c) => c.nome },
    { nome: "Usa", valor: (c) => (c.fonte === "modelo" ? "modelo treinado" : "regra") },
    { nome: "Marcados é / não é", valor: (c) => `${inteiro(c.positivos)} / ${inteiro(c.negativos)}`, num: true },
    { nome: "Precisão estimada", valor: (c) => (c.precisao_cv === null ? "-" : pct(c.precisao_cv, 0)), num: true, classe: (c) => (c.aviso ? "subindo" : "") },
    { nome: "Regra nas marcações", valor: (c) => (c.precisao_regra === null ? "-" : pct(c.precisao_regra, 0)), num: true },
    { nome: "Como decide", valor: (c) => (c.fonte === "modelo" ? "mais peso: " + c.pesos.map((p) => `${p.feature} (${p.peso > 0 ? "+" : "−"})`).join(", ") : c.regra), classe: () => "mapa" },
  ], s.classificador);
  const avisos = s.classificador.filter((c) => c.aviso);
  $("p-avisos-classificador").replaceChildren(...avisos.map((c) => {
    const p = document.createElement("p"); p.className = "aviso"; p.textContent = `${c.nome}: ${c.aviso}`; return p;
  }));
  tabelaMarcacao("t-para-marcar", s.para_marcar, true);
  $("v-para-marcar").textContent = "Nenhum mapa para marcar: todos os mapas lidos já têm marcação, ou nenhum .osu foi lido ainda.";
  $("v-para-marcar").hidden = s.para_marcar.length > 0;
  tabelaMarcacao("t-todos-mapas", s.mapas, false);
}

function confiancas(m) {
  return TIPOS.filter((t) => m.por_tipo[t]).map((t) => {
    const i = m.por_tipo[t];
    return `${NOME_TIPO[t]} ${i.sim ? "sim" : "não"} ${pct(i.confianca, 0)} (${i.fonte})`;
  }).join(" · ");
}

function tabelaMarcacao(id, mapas, comDuvida) {
  const t = $(id);
  t.replaceChildren();
  if (!mapas.length) return;
  const cab = t.createTHead().insertRow();
  const nomes = ["Mapa", "★", "Tipo agora", "Confiança por tipo", ...(comDuvida ? ["Mais dúvida em"] : []), "Marcar como", ""];
  for (const n of nomes) { const th = document.createElement("th"); th.textContent = n; if (n === "★") th.className = "num"; cab.appendChild(th); }
  const corpo = t.createTBody();
  for (const m of mapas) {
    const tr = corpo.insertRow();
    const c0 = tr.insertCell(); c0.className = "mapa"; c0.textContent = `${m.mapa} (${m.beatmap_id})`;
    const c1 = tr.insertCell(); c1.className = "num"; c1.textContent = num(m.estrelas, 2);
    tr.insertCell().textContent = (m.tipos.length ? m.tipos.map((x) => NOME_TIPO[x]).join(", ") : "nenhum") + (m.marcado ? " (marcado)" : "");
    const c3 = tr.insertCell(); c3.className = "mapa confiancas"; c3.textContent = Object.keys(m.por_tipo).length ? confiancas(m) : "ainda sem o .osu";
    if (comDuvida) tr.insertCell().textContent = `${NOME_TIPO[m.duvida_tipo]} (${pct(m.duvida_confianca, 0)})`;
    const c4 = tr.insertCell(); c4.className = "marcar";
    const caixas = TIPOS.map((tipo) => {
      const rot = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = tipo; cb.checked = m.tipos.includes(tipo);
      rot.append(cb, " " + NOME_TIPO[tipo]);
      c4.appendChild(rot);
      return cb;
    });
    const c5 = tr.insertCell(); c5.className = "acoes";
    const salvar = document.createElement("button");
    salvar.type = "button"; salvar.textContent = "Salvar";
    salvar.addEventListener("click", () => enviarMarcacao(m.beatmap_id, caixas.filter((cb) => cb.checked).map((cb) => cb.value), false, salvar));
    c5.appendChild(salvar);
    if (m.marcado) {
      const limpar = document.createElement("button");
      limpar.type = "button"; limpar.className = "secundario"; limpar.textContent = "Limpar";
      limpar.title = "Tira a sua marcação: o mapa volta para o classificador";
      limpar.addEventListener("click", () => enviarMarcacao(m.beatmap_id, [], true, limpar));
      c5.appendChild(limpar);
    }
  }
}

async function enviarMarcacao(beatmapId, tipos, limpar, botao) {
  botao.disabled = true;
  try {
    const r = await fetch("/api/marcacoes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beatmap_id: beatmapId, tipos, limpar }),
    });
    if (!r.ok) {
      const corpo = await r.json().catch(() => ({}));
      throw new Error(corpo.detail || `o servidor respondeu ${r.status}`);
    }
    await carregarProgresso(true);
  } catch (e) {
    botao.disabled = false;
    alert(`Não consegui salvar a marcação: ${e.message}`);
  }
}

// --- carga ---

function redesenharProgresso() {
  const d = prog.dados;
  if (!d || !d.perfil || $("painel-progresso").hidden) return;
  perfil(d.perfil);
  estrelas(d.semanas, d.janelas);
  habilidades(d.semanas, d.janelas);
  skillsets(d.skillsets);
  classificador(d.skillsets);
}

async function carregarProgresso(forcar = false) {
  if (prog.carregando || (prog.dados && !forcar)) { redesenharProgresso(); return; }
  prog.carregando = true;
  let d;
  try {
    const r = await fetch("/api/progresso");
    if (!r.ok) throw new Error(`o servidor respondeu ${r.status}`);
    d = await r.json();
  } catch (e) {
    d = { aviso: `Não consegui falar com o osu-tracker (${e.message}). Ele ainda está aberto?` };
  } finally {
    prog.carregando = false;
  }
  prog.dados = d;
  $("p-aviso").textContent = d.aviso || "";
  $("p-aviso").hidden = !d.aviso;
  for (const s of document.querySelectorAll("#painel-progresso > section")) s.hidden = !d.perfil;
  redesenharProgresso();
  document.body.dataset.progresso = "1";
}

window.carregarProgresso = carregarProgresso;
window.redesenharProgresso = redesenharProgresso;

import { useState, useEffect, useRef, useCallback } from 'react';
import './index.css';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const STREAM_IDLE_TIMEOUT_MS = 180000;

const escapeFormula = (text) => (/^[=+\-@\t\r]/.test(text) ? `'${text}` : text);

async function fetchSchema() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/schema`);
    if (!res.ok) return null;
    const data = await res.json();
    return data && data.success && Array.isArray(data.tables) ? data.tables : null;
  } catch {
    return null;
  }
}

function App() {
  const [theme, setTheme] = useState('light');
  const [sidebarWidth, setSidebarWidth] = useState(232);
  const [isDragging, setIsDragging] = useState(false);

  const [tables, setTables] = useState([]);
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [dbConnected, setDbConnected] = useState(false);

  const [question, setQuestion] = useState('');
  const [sqlQuery, setSqlQuery] = useState('');
  const [sqlValid, setSqlValid] = useState(true);
  const [tableData, setTableData] = useState([]);
  const [explanation, setExplanation] = useState('');

  const [isGenerating, setIsGenerating] = useState(false);
  const [isExplaining, setIsExplaining] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [totalMs, setTotalMs] = useState(null);
  const [sqlMs, setSqlMs] = useState(null);
  const [copied, setCopied] = useState(false);

  const [steps, setSteps] = useState([]);
  const [streamStatus, setStreamStatus] = useState('idle'); // idle | running | done | error
  const esRef = useRef(null);
  const watchdogRef = useRef(null);
  const explainAbortRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    return () => {
      if (esRef.current) esRef.current.close();
      if (watchdogRef.current) clearTimeout(watchdogRef.current);
      if (explainAbortRef.current) explainAbortRef.current.abort();
    };
  }, []);

  const applySchema = useCallback((result) => {
    setTables(result || []);
    setDbConnected(!!result);
    setSchemaLoading(false);
  }, []);

  const loadSchema = useCallback(async () => {
    setSchemaLoading(true);
    applySchema(await fetchSchema());
  }, [applySchema]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const result = await fetchSchema();
      if (!cancelled) applySchema(result);
    })();
    return () => {
      cancelled = true;
    };
  }, [applySchema]);

  const startResize = (e) => {
    e.preventDefault();
    setIsDragging(true);
    const onMove = (ev) => {
      setSidebarWidth(Math.min(520, Math.max(180, ev.clientX)));
    };
    const onUp = () => {
      setIsDragging(false);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
    };
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  const closeStream = () => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setIsGenerating(false);
  };

  const handleStop = () => {
    closeStream();
    setStreamStatus('idle');
    setSteps((prev) => [...prev, { message: 'Đã dừng theo yêu cầu' }]);
  };

  const handleGenerate = () => {
    const q = question.trim();
    if (q.length < 5) {
      setErrorMsg('Vui lòng nhập câu hỏi ít nhất 5 ký tự.');
      return;
    }
    if (q.length > 500) {
      setErrorMsg('Câu hỏi quá dài, tối đa 500 ký tự.');
      return;
    }
    closeStream();

    setErrorMsg('');
    setExplanation('');
    setSqlQuery('');
    setSqlValid(true);
    setTableData([]);
    setTotalMs(null);
    setSqlMs(null);
    setSteps([]);
    setStreamStatus('running');
    setIsGenerating(true);

    const t0 = performance.now();
    const url = `${API_BASE}/api/v1/query/stream?question=${encodeURIComponent(q)}`;
    const es = new EventSource(url);
    esRef.current = es;
    let finished = false;

    const stop = () => {
      finished = true;
      closeStream();
    };

    // Automatically bail out if no event is received for too long
    const armWatchdog = () => {
      if (watchdogRef.current) clearTimeout(watchdogRef.current);
      watchdogRef.current = setTimeout(() => {
        if (finished) return;
        setErrorMsg('Máy chủ không phản hồi. Kiểm tra Ollama rồi thử lại.');
        setStreamStatus('error');
        stop();
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    armWatchdog();

    es.onmessage = (e) => {
      armWatchdog();
      let ev;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      if (ev.type === 'step') {
        if (ev.step === 'generating' || ev.step === 'healing') setSqlQuery('');
        setSteps((prev) => [...prev, { message: ev.message }]);
      } else if (ev.type === 'token') {
        setSqlQuery((prev) => prev + ev.content);
      } else if (ev.type === 'sql') {
        setSqlQuery(ev.sql);
      } else if (ev.type === 'result') {
        setTableData(Array.isArray(ev.data) ? ev.data : []);
        setSqlQuery(ev.sql_query || '');
        setSqlValid(true);
        setSqlMs(ev.exec_ms ?? null);
        setTotalMs(Math.round(performance.now() - t0));
        setStreamStatus('done');
        setDbConnected(true);
        stop();
      } else if (ev.type === 'error') {
        // Mark the generated SQL as incomplete
        setSqlValid(false);
        setErrorMsg(ev.detail || 'Có lỗi xảy ra.');
        setStreamStatus('error');
        stop();
      }
    };

    es.onerror = () => {
      if (!finished) {
        setErrorMsg('Mất kết nối tới máy chủ khi đang xử lý.');
        setStreamStatus('error');
        setDbConnected(false);
        stop();
      }
    };
  };

  const readError = async (res, fallback) => {
    try {
      const data = await res.json();
      return data.detail || fallback;
    } catch {
      return fallback;
    }
  };

  const handleExplain = async () => {
    const sql = sqlQuery.trim();
    if (sql.length < 10) {
      setErrorMsg('Chưa có câu SQL để giải thích.');
      return;
    }
    if (explainAbortRef.current) explainAbortRef.current.abort();
    const controller = new AbortController();
    explainAbortRef.current = controller;

    setErrorMsg('');
    setIsExplaining(true);
    setExplanation('');
    try {
      const res = await fetch(`${API_BASE}/api/v1/explain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
        signal: controller.signal,
      });
      if (!res.ok) {
        setErrorMsg(await readError(res, 'Không giải thích được câu SQL.'));
        return;
      }
      const data = await res.json();
      setExplanation(data.explanation || '');
    } catch (e) {
      if (e.name !== 'AbortError') setErrorMsg('Không kết nối được tới máy chủ.');
    } finally {
      if (explainAbortRef.current === controller) explainAbortRef.current = null;
      setIsExplaining(false);
    }
  };

  const handleRunSql = async () => {
    const sql = sqlQuery.trim();
    if (sql.length < 10) {
      setErrorMsg('Chưa có câu SQL để chạy.');
      return;
    }
    setErrorMsg('');
    setExplanation('');
    setIsRunning(true);
    const t0 = performance.now();
    try {
      const res = await fetch(`${API_BASE}/api/v1/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      });
      if (!res.ok) {
        setErrorMsg(await readError(res, 'Không chạy được câu SQL.'));
        setTableData([]);
        return;
      }
      const data = await res.json();
      setTableData(Array.isArray(data.data) ? data.data : []);
      setSqlQuery(data.sql_query || sql);
      setSqlValid(true);
      setSqlMs(data.exec_ms ?? null);
      setTotalMs(Math.round(performance.now() - t0));
      setSteps([]);
      setStreamStatus('idle');
    } catch {
      setErrorMsg('Không kết nối được tới máy chủ.');
    } finally {
      setIsRunning(false);
    }
  };

  const handleCopy = async () => {
    if (!sqlQuery.trim()) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(sqlQuery);
      } else {
        // Fallback copy method when the Clipboard API is unavailable
        const ta = document.createElement('textarea');
        ta.value = sqlQuery;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setErrorMsg('Trình duyệt không cho phép copy. Hãy bôi đen câu SQL rồi copy tay.');
    }
  };

  const handleFormat = () => {
    if (!sqlQuery.trim()) return;
    const parts = sqlQuery.split(/('(?:''|[^'])*')/);
    const keywords =
      /\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION ALL|UNION|(?:LEFT|RIGHT|INNER|FULL|CROSS)(?:\s+OUTER)?\s+JOIN|JOIN)\b/gi;
    const formatted = parts
      .map((part, i) =>
        i % 2
          ? part
          : part.replace(/\s+/g, ' ').replace(keywords, (_, kw) => '\n' + kw.toUpperCase())
      )
      .join('');
    setSqlQuery(formatted.trim());
  };

  const handleClear = () => {
    closeStream();
    if (explainAbortRef.current) {
      explainAbortRef.current.abort();
      explainAbortRef.current = null;
    }
    setQuestion('');
    setSqlQuery('');
    setSqlValid(true);
    setTableData([]);
    setExplanation('');
    setErrorMsg('');
    setTotalMs(null);
    setSqlMs(null);
    setSteps([]);
    setStreamStatus('idle');
    setIsExplaining(false);
    setIsRunning(false);
  };

  const downloadBlob = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const handleExportCsv = () => {
    if (!tableData.length) return;
    const cols = Object.keys(tableData[0]);
    const esc = (v) =>
      v == null ? '' : '"' + escapeFormula(String(v)).replace(/"/g, '""') + '"';
    const csv = [
      cols.join(','),
      ...tableData.map((row) => cols.map((c) => esc(row[c])).join(',')),
    ].join('\n');
    // BOM so Excel reads UTF-8 correctly; sep= line so Excel splits columns correctly
    const blob = new Blob(['\uFEFF' + 'sep=,\n' + csv], { type: 'text/csv;charset=utf-8;' });
    downloadBlob(blob, 'ket_qua.csv');
  };

  const handleExportJson = () => {
    if (!tableData.length) return;
    const blob = new Blob([JSON.stringify(tableData, null, 2)], {
      type: 'application/json;charset=utf-8;',
    });
    downloadBlob(blob, 'ket_qua.json');
  };

  const handleExportExcel = () => {
    if (!tableData.length) return;
    const cols = Object.keys(tableData[0]);
    const esc = (v) =>
      v == null
        ? ''
        : escapeFormula(String(v))
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    const head = '<tr>' + cols.map((c) => `<th>${esc(c)}</th>`).join('') + '</tr>';
    const rows = tableData
      .map((r) => '<tr>' + cols.map((c) => `<td>${esc(r[c])}</td>`).join('') + '</tr>')
      .join('');
    const doc =
      '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
      'xmlns:x="urn:schemas-microsoft-com:office:excel">' +
      '<head><meta charset="UTF-8"></head><body>' +
      `<table border="1"><thead>${head}</thead><tbody>${rows}</tbody></table>` +
      '</body></html>';
    const blob = new Blob(['\uFEFF' + doc], { type: 'application/vnd.ms-excel;charset=utf-8;' });
    downloadBlob(blob, 'ket_qua.xls');
  };

  const columns = tableData.length ? Object.keys(tableData[0]) : [];
  const sqlBusy = isGenerating || isRunning || isExplaining;
  const canUseSql = sqlValid && !!sqlQuery.trim();

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="brand-name">Text-to-SQL</span>
          <span className="brand-sub">Quản lý hợp đồng XKLĐ</span>
        </div>
        <div className="header-right">
          <div className="conn">
            <span
              className="conn-dot"
              style={{ background: dbConnected ? 'var(--success)' : 'var(--text-muted)' }}
            />
            {dbConnected ? 'PostgreSQL · do_an' : 'Chưa kết nối'}
          </div>
          <button
            className="toggle"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
          >
            {theme === 'light' ? 'Chế độ tối' : 'Chế độ sáng'}
          </button>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar" style={{ width: sidebarWidth }}>
          <div className="sidebar-title">Bảng dữ liệu</div>
          {schemaLoading ? (
            <div className="sidebar-empty">Đang kết nối cơ sở dữ liệu…</div>
          ) : tables.length === 0 ? (
            <div className="sidebar-empty">
              Chưa kết nối được backend.
              <button className="btn btn-sm" onClick={loadSchema} style={{ marginTop: 8 }}>
                Thử lại
              </button>
            </div>
          ) : (
            tables.map((t) => (
              <div className="table-item" key={t.name}>
                <div className="table-name mono">{t.name}</div>
                <div className="table-desc">{t.columns ? `${t.columns.length} cột` : ''}</div>
              </div>
            ))
          )}
        </aside>
        <div
          className={`resizer${isDragging ? ' dragging' : ''}`}
          onMouseDown={startResize}
          title="Kéo để giãn/thu bảng dữ liệu"
        />

        <main className="main">
          {/* Ask a question */}
          <section className="section">
            <div className="section-head">
              <div className="section-title">Đặt câu hỏi bằng tiếng Việt</div>
            </div>
            <textarea
              className="question-input"
              placeholder="Ví dụ: Liệt kê các công ty có giấy phép xuất khẩu lao động"
              value={question}
              maxLength={500}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !isGenerating) {
                  handleGenerate();
                }
              }}
            />
            <div className="row-actions">
              {isGenerating ? (
                <button className="btn btn-primary" onClick={handleStop}>
                  <span className="spinner" />
                  Dừng
                </button>
              ) : (
                <button className="btn btn-primary" onClick={handleGenerate}>
                  Generate SQL
                </button>
              )}
              <button className="btn" onClick={handleClear}>
                Xóa
              </button>
            </div>
          </section>

          {/* Model processing progress (shown while running/after it ran) */}
          {steps.length > 0 && (
            <section className="section">
              <div className="section-head">
                <div className="section-title">Tiến trình</div>
                {totalMs != null && (
                  <div className="section-meta">tổng {totalMs} ms</div>
                )}
              </div>
              <div className="process">
                {steps.map((s, i) => {
                  const status =
                    i < steps.length - 1
                      ? 'done'
                      : streamStatus === 'running'
                        ? 'running'
                        : streamStatus;
                  return (
                    <div className={`process-step ${status}`} key={i}>
                      <span className="process-ind" />
                      <span className="process-msg">{s.message}</span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {errorMsg && <div className="error-box">{errorMsg}</div>}

          {/* SQL statement */}
          <section className="section">
            <div className="section-head">
              <div className="section-title">Câu lệnh SQL</div>
              {!sqlValid && sqlQuery.trim() && (
                <div className="section-meta">chưa hợp lệ, cần sửa trước khi chạy</div>
              )}
            </div>
            <textarea
              className="sql-editor"
              placeholder="-- SQL được sinh ra sẽ hiển thị ở đây"
              value={sqlQuery}
              onChange={(e) => {
                setSqlQuery(e.target.value);
                setSqlValid(true);
              }}
              spellCheck={false}
            />
            <div className="row-actions">
              <button
                className="btn btn-sm"
                onClick={handleRunSql}
                disabled={sqlBusy || !canUseSql}
              >
                {isRunning ? 'Đang chạy' : 'Chạy SQL'}
              </button>
              <button
                className="btn btn-sm"
                onClick={handleExplain}
                disabled={sqlBusy || !canUseSql}
              >
                {isExplaining ? 'Đang giải thích' : 'Giải thích'}
              </button>
              <button className="btn btn-sm" onClick={handleCopy} disabled={!sqlQuery.trim()}>
                {copied ? 'Đã copy' : 'Copy'}
              </button>
              <button className="btn btn-sm" onClick={handleFormat} disabled={!sqlQuery.trim()}>
                Format
              </button>
            </div>
            {explanation && (
              <div className="explain-box">
                <span className="explain-label">Giải thích:</span>
                {explanation}
              </div>
            )}
          </section>

          {/* Results */}
          <section className="section">
            <div className="section-head">
              <div className="section-title">Kết quả</div>
              <div className="section-meta">
                {tableData.length} dòng
                {sqlMs != null ? ` · SQL chạy ${sqlMs} ms` : ''}
              </div>
            </div>
            <div className="table-wrap">
              {tableData.length === 0 ? (
                <div className="empty">Chưa có dữ liệu. Nhập câu hỏi và bấm Generate SQL.</div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      {columns.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tableData.map((row, i) => (
                      <tr key={i}>
                        {columns.map((c) => (
                          <td key={c}>{row[c] === null ? 'NULL' : String(row[c])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            {tableData.length > 0 && (
              <div className="export-bar">
                <span className="export-label">Xuất kết quả:</span>
                <button className="btn btn-sm" onClick={handleExportCsv}>CSV</button>
                <button className="btn btn-sm" onClick={handleExportExcel}>Excel</button>
                <button className="btn btn-sm" onClick={handleExportJson}>JSON</button>
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}

export default App;

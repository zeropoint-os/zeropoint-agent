import React, { useEffect, useState } from 'react';

export default function App() {
  const [status, setStatus] = useState<string>('unknown');
  const [timestamp, setTimestamp] = useState<string>('');

  useEffect(() => {
    fetch('/health')
      .then((r) => r.json())
      .then((json) => {
        setStatus(json.status ?? 'ok');
        setTimestamp(json.timestamp ?? '');
      })
      .catch(() => {
        setStatus('unreachable');
      });
  }, []);

  return (
    <div style={{ padding: 24, color: '#fff' }}>
      <h1>Zeropoint Minimal WebUI</h1>
      <p>Status: <strong>{status}</strong></p>
      <p>Timestamp: <strong>{timestamp}</strong></p>
      <p>Build a UI here or connect to the Live LLM server.</p>
    </div>
  );
}

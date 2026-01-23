import React from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './components/App';
import './index.css';

const container = document.getElementById('root');
if (container)
{
  const root = createRoot(container);
  root.render(
    <HashRouter>
      <App />
    </HashRouter>
  );
}
import React, { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import '../styles/theme.css';
import '../styles/layout.css';
import Header from './Header';
import Navigation from './Navigation';
import ModulesView from '../views/ModulesView';
import LinksView from '../views/LinksView';
import ExposuresView from '../views/ExposuresView';
import CatalogView from '../views/CatalogView';
import BundlesView from '../views/BundlesView';

type ViewType = 'modules' | 'links' | 'exposures' | 'catalog' | 'bundles';

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [isDark, setIsDark] = useState(() => {
    // Check localStorage or system preference
    const saved = localStorage.getItem('theme');
    if (saved) return saved === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  // Get current view from URL path
  const getCurrentView = (): ViewType => {
    const path = location.pathname;
    if (path === '/') return 'modules';
    const view = path.substring(1) as ViewType;
    const validViews: ViewType[] = ['modules', 'links', 'exposures', 'catalog', 'bundles'];
    return validViews.includes(view) ? view : 'modules';
  };

  const currentView = getCurrentView();

  // Update theme
  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
  }, [isDark]);

  // Close nav when view changes on mobile
  const handleViewChange = (view: ViewType) => {
    navigate(`/${view === 'modules' ? '' : view}`);
    setNavOpen(false);
  };

  const toggleTheme = () => {
    setIsDark(!isDark);
  };

  return (
    <div className="app">
      <Header
        navOpen={navOpen}
        onNavToggle={() => setNavOpen(!navOpen)}
        isDark={isDark}
        onThemeToggle={toggleTheme}
      />
      <Navigation
        isOpen={navOpen}
        currentView={currentView}
        onViewChange={handleViewChange}
      />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<ModulesView />} />
          <Route path="/modules" element={<ModulesView />} />
          <Route path="/links" element={<LinksView />} />
          <Route path="/exposures" element={<ExposuresView />} />
          <Route path="/catalog" element={<CatalogView />} />
          <Route path="/bundles" element={<BundlesView />} />
        </Routes>
      </main>
    </div>
  );
}

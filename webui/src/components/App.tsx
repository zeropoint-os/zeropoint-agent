import React, { useState, useEffect } from 'react';
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
  const [currentView, setCurrentView] = useState<ViewType>('modules');
  const [navOpen, setNavOpen] = useState(false);
  const [isDark, setIsDark] = useState(() => {
    // Check localStorage or system preference
    const saved = localStorage.getItem('theme');
    if (saved) return saved === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

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
    setCurrentView(view);
    setNavOpen(false);
  };

  const toggleTheme = () => {
    setIsDark(!isDark);
  };

  const renderView = () => {
    switch (currentView) {
      case 'modules':
        return <ModulesView />;
      case 'links':
        return <LinksView />;
      case 'exposures':
        return <ExposuresView />;
      case 'catalog':
        return <CatalogView />;
      case 'bundles':
        return <BundlesView />;
      default:
        return <ModulesView />;
    }
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
        {renderView()}
      </main>
    </div>
  );
}

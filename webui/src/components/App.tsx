import React, { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import '../styles/theme.css';
import '../styles/layout.css';
import Header from './Header';
import Navigation from './Navigation';
import BootView from '../views/BootView';
import ModulesView from '../views/ModulesView';
import LinksView from '../views/LinksView';
import ExposuresView from '../views/ExposuresView';
import BundlesView from '../views/BundlesView';
import { BootApi, Configuration } from 'artifacts/clients/typescript';

type ViewType = 'boot' | 'modules' | 'links' | 'exposures' | 'bundles';

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
  const [bootComplete, setBootComplete] = useState(false);
  const [checkingBoot, setCheckingBoot] = useState(true);

  const bootApi = new BootApi(new Configuration({ basePath: '' }));

  // Check boot status and redirect if needed
  useEffect(() => {
    const checkBoot = async () => {
      try {
        const data = await bootApi.getBootStatus();
        // Check if boot-complete marker exists
        let isComplete = false;
        if (data && Array.isArray(data)) {
          const lastService = data[data.length - 1];
          if (lastService?.service?.includes('boot-complete')) {
            const hasBootCompleteMarker = (lastService.markers || []).some(m => m.step === 'boot-complete');
            isComplete = hasBootCompleteMarker;
          }
        }
        setBootComplete(isComplete);
      } catch (err) {
        console.error('Failed to check boot status:', err);
      } finally {
        setCheckingBoot(false);
      }
    };

    checkBoot();
  }, [bootApi]);

  // Get current view from URL path
  const getCurrentView = (): ViewType => {
    const path = location.pathname;
    if (path === '/') {
      // Root redirects to first nav item: boot if not complete, bundles if complete
      return bootComplete ? 'bundles' : 'boot';
    }
    const view = path.substring(1) as ViewType;
    const validViews: ViewType[] = ['boot', 'modules', 'links', 'exposures', 'bundles'];
    return validViews.includes(view) ? view : 'boot';
  };

  const currentView = getCurrentView();

  // If boot is not complete and user tries to navigate to non-boot page, redirect to boot
  useEffect(() => {
    if (!checkingBoot && !bootComplete && currentView !== 'boot') {
      navigate('/boot');
    }
  }, [bootComplete, checkingBoot, currentView, navigate]);

  // Redirect root path to first nav item
  useEffect(() => {
    if (!checkingBoot && location.pathname === '/') {
      const firstItem = bootComplete ? 'bundles' : 'boot';
      navigate(`/${firstItem === 'boot' ? 'boot' : firstItem}`);
    }
  }, [bootComplete, checkingBoot, navigate, location.pathname]);

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
    // Block navigation away from boot if boot is not complete
    if (!bootComplete && view !== 'boot') {
      return;
    }
    navigate(`/${view === 'boot' ? '' : view}`);
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
        bootComplete={bootComplete}
      />
      <main className="main-content">
        <Routes>
          <Route path="/boot" element={<BootView />} />
          <Route path="/modules" element={<ModulesView />} />
          <Route path="/links" element={<LinksView />} />
          <Route path="/exposures" element={<ExposuresView />} />
          <Route path="/bundles" element={<BundlesView />} />
        </Routes>
      </main>
    </div>
  );
}

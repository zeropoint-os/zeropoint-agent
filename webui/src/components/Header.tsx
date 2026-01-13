import React from 'react';
import './Header.css';

interface HeaderProps {
  navOpen: boolean;
  onNavToggle: () => void;
  isDark: boolean;
  onThemeToggle: () => void;
}

export default function Header({
  navOpen,
  onNavToggle,
  isDark,
  onThemeToggle,
}: HeaderProps) {
  return (
    <header className="header">
      <div className="header-content">
        <div className="header-left">
          <button
            className={`hamburger ${navOpen ? 'active' : ''}`}
            onClick={onNavToggle}
            aria-label="Toggle navigation"
            title="Toggle navigation"
          >
            <span></span>
            <span></span>
            <span></span>
          </button>
          <div className="logo">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <circle cx="12" cy="12" r="1"></circle>
              <circle cx="19" cy="12" r="1"></circle>
              <circle cx="5" cy="12" r="1"></circle>
              <path d="M12 5v14M19 5v14M5 5v14"></path>
            </svg>
            <span className="logo-text">Zeropoint</span>
          </div>
        </div>
        <div className="header-right">
          <button
            className="theme-toggle"
            onClick={onThemeToggle}
            aria-label="Toggle theme"
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {isDark ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="12" r="5"></circle>
                <line x1="12" y1="1" x2="12" y2="3"></line>
                <line x1="12" y1="21" x2="12" y2="23"></line>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                <line x1="1" y1="12" x2="3" y2="12"></line>
                <line x1="21" y1="12" x2="23" y2="12"></line>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
              </svg>
            )}
          </button>
        </div>
      </div>
    </header>
  );
}

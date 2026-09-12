import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { MapPin, Menu, X } from 'lucide-react';
import { API_DOCS_URL } from '../services/api';

function Layout({ children }) {
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);
  const location = useLocation();
  const isAnalysisRoute = location.pathname.startsWith('/analysis/');

  return (
    <div className={`${isAnalysisRoute ? 'h-dvh overflow-hidden' : 'min-h-screen'} flex flex-col bg-gray-50`}>
      {/* Header */}
      <header className="shrink-0 bg-white border-b border-gray-200">
        <div className="mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            {/* Logo */}
            <Link to="/" className="flex items-center space-x-3">
              <div className="w-9 h-9 bg-primary rounded-lg flex items-center justify-center">
                <MapPin className="w-5 h-5 text-white" />
              </div>
              <span className="text-lg font-semibold text-gray-900">
                NJ High Injury Network
              </span>
            </Link>

            {/* Desktop Navigation */}
            <nav className="hidden md:flex items-center space-x-8">
              <Link
                to="/"
                className={`text-sm font-medium transition-colors ${
                  location.pathname === '/'
                    ? 'text-primary'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Home
              </Link>
              <a
                href={API_DOCS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-medium text-gray-600 hover:text-gray-900 transition-colors"
              >
                API Docs
              </a>
            </nav>

            {/* Mobile menu button */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              aria-label={mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={mobileMenuOpen}
              aria-controls="mobile-navigation"
              className="md:hidden p-2 min-h-11 min-w-11 rounded-lg text-gray-600 hover:bg-gray-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            >
              {mobileMenuOpen ? (
                <X className="w-6 h-6" />
              ) : (
                <Menu className="w-6 h-6" />
              )}
            </button>
          </div>
        </div>

        {/* Mobile Navigation */}
        {mobileMenuOpen && (
          <div id="mobile-navigation" className="md:hidden border-t border-gray-200">
            <nav className="px-4 py-4 space-y-3">
              <Link
                to="/"
                onClick={() => setMobileMenuOpen(false)}
                className={`block text-sm font-medium ${
                  location.pathname === '/'
                    ? 'text-primary'
                    : 'text-gray-600'
                }`}
              >
                Home
              </Link>
              <a
                href={API_DOCS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-sm font-medium text-gray-600"
              >
                API Docs
              </a>
            </nav>
          </div>
        )}
      </header>

      {/* Main Content */}
      <main className={isAnalysisRoute ? 'flex flex-1 min-h-0 min-w-0 overflow-hidden' : 'flex-1'}>
        {children}
      </main>
    </div>
  );
}

export default Layout;

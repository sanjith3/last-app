// ============================================
// TrufSpot Landing Page — Scripts
// ============================================

document.addEventListener('DOMContentLoaded', () => {

    /* ---------- Navbar scroll effect ---------- */
    const navbar = document.querySelector('.navbar');
    const scrollThreshold = 60;

    const handleScroll = () => {
        navbar.classList.toggle('scrolled', window.scrollY > scrollThreshold);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll(); // initial check

    /* ---------- Mobile menu ---------- */
    const hamburger = document.querySelector('.hamburger');
    const mobileNav = document.querySelector('.mobile-nav');
    const overlay = document.querySelector('.mobile-overlay');
    const mobileLinks = mobileNav.querySelectorAll('a');

    const toggleMenu = (open) => {
        const isOpen = typeof open === 'boolean' ? open : !mobileNav.classList.contains('open');
        hamburger.classList.toggle('active', isOpen);
        mobileNav.classList.toggle('open', isOpen);
        overlay.classList.toggle('show', isOpen);
        document.body.style.overflow = isOpen ? 'hidden' : '';
    };

    hamburger.addEventListener('click', () => toggleMenu());
    overlay.addEventListener('click', () => toggleMenu(false));
    mobileLinks.forEach(link => link.addEventListener('click', () => toggleMenu(false)));

    /* ---------- Smooth scroll for anchor links ---------- */
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', (e) => {
            const target = document.querySelector(anchor.getAttribute('href'));
            if (!target) return;
            e.preventDefault();
            const offset = 70; // navbar height offset
            const top = target.getBoundingClientRect().top + window.scrollY - offset;
            window.scrollTo({ top, behavior: 'smooth' });
        });
    });

    /* ---------- Intersection Observer — fade-in on scroll ---------- */
    const fadeEls = document.querySelectorAll('.fade-in');

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.15 });

        fadeEls.forEach(el => observer.observe(el));
    } else {
        // Fallback — just show everything
        fadeEls.forEach(el => el.classList.add('visible'));
    }

    /* ---------- Active nav link on scroll ---------- */
    const sections = document.querySelectorAll('section[id]');
    const navLinks = document.querySelectorAll('.nav-links a');

    const highlightNav = () => {
        const scrollY = window.scrollY + 120;
        sections.forEach(section => {
            const top = section.offsetTop;
            const height = section.offsetHeight;
            const id = section.getAttribute('id');
            if (scrollY >= top && scrollY < top + height) {
                navLinks.forEach(l => l.classList.remove('active'));
                const active = document.querySelector(`.nav-links a[href="#${id}"]`);
                if (active) active.classList.add('active');
            }
        });
    };
    window.addEventListener('scroll', highlightNav, { passive: true });
});

// Moonraker authentication: Get JWT from Fluidd/Mainsail localStorage
function getJWT() {
    // Fluidd stores tokens as "user-token-{hash}" where hash is based on the instance
    
    // First try: instance-specific token (most secure)
    const instanceKey = `user-token-${window.location.host.replace(/[^a-zA-Z0-9]/g, '_')}`;
    let token = localStorage.getItem(instanceKey);
    if (token) return token;
    
    // Second try: search for any Fluidd user-token pattern
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith('user-token-')) {
            token = localStorage.getItem(key);
            if (token) return token;
        }
    }
    
    return null;
}

// Check authentication with Moonraker
async function checkAuth() {
    const jwt = getJWT();
    
    const headers = {};
    if (jwt) {
        headers['Authorization'] = `Bearer ${jwt}`;
    }

    try {
        const response = await fetch('/access/oneshot_token', {
            headers: headers
        });

        if (!response.ok) {
            // If 401, redirect to login
            if (response.status === 401) {
                console.log('Authentication required, redirecting to login...');
                window.location.href = '/';
                return false;
            }
            console.warn(`Auth check returned: ${response.status} ${response.statusText}`);
            return false;
        }

        // Verify response contains valid token (works with auth enabled or disabled)
        const data = await response.json();
        const token = data.result || data;
        
        if (!token) {
            console.error('Invalid auth response: no token received');
            return false;
        }

        return true;
    } catch (err) {
        console.error("Authentication error:", err);
        return false;
    }
}

// Initialize authentication check on page load
async function initAuth() {
    const authenticated = await checkAuth();
    if (!authenticated) {
        console.log('Authentication check failed');
    }
    return authenticated;
}

// Get auth headers for API requests
function getAuthHeaders() {
    const jwt = getJWT();
    const headers = {};
    if (jwt) {
        headers['Authorization'] = `Bearer ${jwt}`;
    }
    return headers;
}

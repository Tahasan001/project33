# Vercel Deployment Guide for ExamAssist

This guide will help you deploy your Django ExamAssist application to Vercel.

## Prerequisites

1. **Vercel Account**: Sign up at [vercel.com](https://vercel.com)
2. **GitHub Account**: Your code should be in a GitHub repository
3. **Environment Variables**: You'll need to set up environment variables

## Files Created/Modified for Deployment

### 1. Configuration Files
- `vercel.json` - Vercel deployment configuration
- `.vercelignore` - Files to exclude from deployment
- `requirements.txt` - Python dependencies
- `.env.example` - Environment variables template

### 2. Django Settings Updates
- Updated `settings.py` to use environment variables
- Configured static files for Vercel
- Set up production-ready database configuration

### 3. Build Script
- `build.sh` - Build script for deployment

## Deployment Steps

### Step 1: Push to GitHub
```bash
git add .
git commit -m "Prepare for Vercel deployment"
git push origin main
```

### Step 2: Deploy to Vercel

#### Option A: Using Vercel CLI
1. Install Vercel CLI:
   ```bash
   npm i -g vercel
   ```

2. Login to Vercel:
   ```bash
   vercel login
   ```

3. Deploy:
   ```bash
   vercel
   ```

#### Option B: Using Vercel Dashboard
1. Go to [vercel.com/dashboard](https://vercel.com/dashboard)
2. Click "New Project"
3. Import your GitHub repository
4. Configure the project:
   - **Framework Preset**: Other
   - **Root Directory**: Leave empty (or set to project root)
   - **Build Command**: `./build.sh`
   - **Output Directory**: Leave empty

### Step 3: Configure Environment Variables

In your Vercel project dashboard:

1. Go to Settings → Environment Variables
2. Add the following variables:

```
SECRET_KEY=your-secret-key-here
DEBUG=False
ALLOWED_HOSTS=your-domain.vercel.app,localhost
GEMINI_API_KEY=your-gemini-api-key-here
```

**Important**: Generate a new secret key for production:
```python
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())
```

### Step 4: Database Considerations

**Current Setup**: Using SQLite (file-based database)
- ✅ Works for development and small projects
- ❌ Not recommended for production with multiple users
- ❌ Data will be lost on each deployment

**Recommended for Production**: PostgreSQL
1. Use a service like:
   - [Neon](https://neon.tech) (Free tier available)
   - [Supabase](https://supabase.com) (Free tier available)
   - [Railway](https://railway.app) (Free tier available)

2. Add to requirements.txt:
   ```
   psycopg2-binary==2.9.9
   dj-database-url==2.1.0
   ```

3. Update settings.py (uncomment the PostgreSQL configuration)

### Step 5: Static Files

Static files are automatically handled by Vercel, but for better performance:
1. Consider using a CDN for media files
2. Optimize images before upload
3. Use Vercel's built-in image optimization

## Post-Deployment

### 1. Run Migrations
After deployment, you may need to run migrations:
```bash
vercel env pull .env.local
python manage.py migrate
```

### 2. Create Superuser
```bash
python manage.py createsuperuser
```

### 3. Test Your Application
1. Visit your Vercel URL
2. Test all functionality
3. Check static files are loading
4. Verify media uploads work

## Troubleshooting

### Common Issues:

1. **Static Files Not Loading**
   - Check `STATIC_URL` and `STATIC_ROOT` settings
   - Ensure `collectstatic` ran during build

2. **Database Errors**
   - Verify database configuration
   - Check if migrations ran successfully

3. **Environment Variables Not Working**
   - Verify variables are set in Vercel dashboard
   - Check variable names match exactly

4. **Import Errors**
   - Ensure all dependencies are in `requirements.txt`
   - Check Python version compatibility

### Debugging:
1. Check Vercel function logs
2. Use `vercel logs` command
3. Enable debug mode temporarily (set `DEBUG=True`)

## Security Considerations

1. **Never commit sensitive data**:
   - Keep `.env` files in `.gitignore`
   - Use environment variables for secrets

2. **Production Settings**:
   - Set `DEBUG=False`
   - Use strong `SECRET_KEY`
   - Configure `ALLOWED_HOSTS` properly

3. **Database Security**:
   - Use connection pooling
   - Enable SSL connections
   - Regular backups

## Performance Optimization

1. **Database**:
   - Use connection pooling
   - Optimize queries
   - Add database indexes

2. **Static Files**:
   - Use CDN for media files
   - Compress images
   - Enable gzip compression

3. **Caching**:
   - Implement Redis caching
   - Use Django's cache framework

## Monitoring

1. **Vercel Analytics**: Built-in performance monitoring
2. **Error Tracking**: Consider Sentry for error monitoring
3. **Uptime Monitoring**: Use services like UptimeRobot

## Next Steps

1. Set up a production database
2. Configure custom domain
3. Set up SSL certificate
4. Implement monitoring and logging
5. Set up automated backups
6. Configure CI/CD pipeline

## Support

- [Vercel Documentation](https://vercel.com/docs)
- [Django Deployment Guide](https://docs.djangoproject.com/en/4.2/howto/deployment/)
- [Vercel Python Support](https://vercel.com/docs/concepts/functions/serverless-functions/runtimes/python)

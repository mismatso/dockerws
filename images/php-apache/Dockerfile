ARG PHP_IMAGE=php:8.4-apache-bookworm
FROM ${PHP_IMAGE}

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends \
       libicu-dev libzip-dev libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
       libonig-dev libpq-dev libsqlite3-dev unzip \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j2 pdo_mysql mysqli pdo_pgsql pdo_sqlite mbstring intl zip gd bcmath \
    && if php -r 'exit(PHP_VERSION_ID >= 80500 ? 1 : 0);'; then docker-php-ext-install opcache; fi \
    && a2enmod rewrite headers remoteip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
COPY apache.conf /etc/apache2/sites-available/000-default.conf
COPY security.conf /etc/apache2/conf-enabled/dockerws-security.conf
COPY workers.conf /etc/apache2/conf-enabled/dockerws-workers.conf
COPY php.ini /usr/local/etc/php/conf.d/production.ini
ENV APP_DOCUMENT_ROOT=/var/www/html
WORKDIR /var/www/html

# Comprueba Apache; no supone que una ruta de aplicación esté disponible.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
 CMD php -r '$s=@fsockopen("127.0.0.1",80,$e,$m,3); if(!$s)exit(1); fclose($s);'

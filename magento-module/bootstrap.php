<?php
declare(strict_types=1);

$magentoRoot = getenv('SKUDO_MAGENTO_ROOT') ?: '/var/www/casanissei.com/v248';
$autoload = $magentoRoot . '/vendor/autoload.php';

if (!is_file($autoload)) {
    fwrite(STDERR, "No se encontró el autoloader de Magento en $autoload\n");
    fwrite(STDERR, "Definí SKUDO_MAGENTO_ROOT apuntando a la raíz de la instalación.\n");
    exit(1);
}

require $autoload;

// El módulo NO se registra en Magento: solo se hace autocargable para los tests.
spl_autoload_register(static function (string $class): void {
    $prefix = 'Standard\\Skudo\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = substr($class, strlen($prefix));
    $path = __DIR__ . '/Standard/Skudo/' . str_replace('\\', '/', $relative) . '.php';
    if (is_file($path)) {
        require $path;
    }
});

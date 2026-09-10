<?php
declare(strict_types=1);

namespace Standard\Skudo\Cron;

use Magento\Framework\App\Config\ScopeConfigInterface;
use Standard\Skudo\Model\ChangeLogRetention;

/**
 * La mitad desatendida de la poda (M7). Ver `etc/crontab.xml` para el horario
 * y `Model\ChangeLogRetention` para lo que borra y lo que nunca borra.
 *
 * Comparte con el comando de consola la resolución del margen —opción no hay
 * acá, así que es la configuración con el default del modelo como respaldo— y
 * NADA más: la regla vive una sola vez, en el modelo.
 */
class PruneChangeLog
{
    public function __construct(
        private readonly ChangeLogRetention $retention,
        private readonly ScopeConfigInterface $scopeConfig,
    ) {
    }

    public function execute(): void
    {
        $configured = $this->scopeConfig->getValue(
            ChangeLogRetention::CONFIG_PATH_SAFETY_DAYS
        );
        $safetyDays = $configured === null || $configured === ''
            ? ChangeLogRetention::DEFAULT_SAFETY_DAYS
            : (int) $configured;

        // Un margen configurado por debajo del piso no debe detener el cron
        // con una excepción cada minuto: se usa el default y se sigue. El
        // comando de consola, que tiene a un humano delante, sí falla.
        if ($safetyDays < ChangeLogRetention::MIN_SAFETY_DAYS) {
            $safetyDays = ChangeLogRetention::DEFAULT_SAFETY_DAYS;
        }

        $this->retention->prune($safetyDays);
    }
}

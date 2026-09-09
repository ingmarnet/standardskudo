<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface EnvironmentProbeInterface
{
    /**
     * Describe el entorno Magento para que el ingestor se adapte a él.
     *
     * @return mixed[]
     */
    public function getProfile(): array;
}
